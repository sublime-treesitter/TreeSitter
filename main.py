"""
Ideally TreeSitter would ideally be a "dependency", see https://packagecontrol.io/docs/dependencies, but dependencies
can't interface with `sublime_plugin` directly. This means no commands and no event listeners. See
https://github.com/SublimeText/sublime_lib/issues/127#issuecomment-516397027.

This means plugins that "depend" on this one, i.e. that do `from sublime_tree_sitter import get_tree_dict`, need to be
loaded after this one, or they need to do `sublime_tree_sitter` imports after this plugin has loaded.

TreeSitter does the following:

- Installs Tree-sitter Python bindings, see https://github.com/tree-sitter/py-tree-sitter
    - Importable by other plugins with `import tree_sitter`
- Installs and builds Tree-sitter languages, e.g. https://github.com/tree-sitter/tree-sitter-python, based on settings
    - Also installs and updates languages on command
- Provides APIs for:
    - Getting a Tree-sitter `Tree` by its buffer id, getting trees for all tracked buffers
    - Subscribing to tree changes in any buffer in real time using `sublime_plugin.EventListener`
    - Getting a tree from a string of code
    - Querying a tree, walking a tree

It's easy to build Tree-sitter plugins on top of this one, for "structural" editing, selection, navigation, code
folding, code maps… See e.g. https://zed.dev/blog/syntax-aware-editing for ideas. It's performant and doesn't block the
main thread.

It has the following limitations:

- It doesn't support nested syntax trees, e.g. JS code in `<script>` tags in HTML docs
- It only supports source code encoded with ASCII / UTF-8 (Tree-sitter also supports UTF-16)
- Due to limitations in Sublime's bundled Python, it requires an external Python 3.8 executable (see settings)
- Due to how syntax highlighting works in Sublime, it can't be used for syntax highlighting
    - See e.g. https://github.com/sublimehq/sublime_text/issues/817
- It breaks if the package is reloaded, which is a nuisance if you're working on this codebase
    - After package is reloaded, `BUFFER_ID_TO_TREE` is no longer updated on buffer changes
"""

from __future__ import annotations

import os
import subprocess
import time
from importlib.util import find_spec
from pathlib import Path
from shutil import rmtree
from threading import Thread
from typing import TYPE_CHECKING, List, TypedDict, cast

import sublime
import sublime_plugin
from sublime import View

from .src.utils import (
    BUILD_PATH,
    DEPS_PATH,
    LANGUAGE_NAME_TO_PATH,
    LANGUAGE_NAME_TO_REPO,
    LANGUAGE_NAME_TO_SCOPES,
    LIB_PATH,
    PROJECT_ROOT,
    SCOPE_TO_LANGUAGE_NAME,
    ScopeType,
    add_path,
    log,
)

if TYPE_CHECKING:
    from tree_sitter import Language, Parser, Tree

TREE_SITTER_BINDINGS_VERSION = "0.20.2"
PROJECT_REPO = "https://github.com/sublime-treesitter/treesitter"
SETTINGS_FILENAME = "TreeSitter.sublime-settings"

MAX_CACHED_TREES = 16
SCOPE_TO_LANGUAGE: dict[ScopeType, Language] = {}

# LRU cache, dict of `(buffer_id, syntax)` tuple keys pointing to dict with tree instance and other metadata.
BUFFER_ID_TO_TREE: dict[int, TreeDict] = {}

# These need to be added to plugin host's `sys.path` before other plugins that depend on them load
add_path(str(LIB_PATH))
add_path(str(DEPS_PATH))


class TreeDict(TypedDict):
    tree: Tree
    scope: ScopeType
    updated_s: float


#
# Code for installing tree sitter, and installing/building languages
#


def get_settings():
    """
    Note that during plugin startup, plugins can't call most `sublime` methods, including `load_settings`.

    [See more here](https://www.sublimetext.com/docs/api_reference.html#plugin-lifecycle).
    """
    return sublime.load_settings(SETTINGS_FILENAME)


def install_tree_sitter(pip_path: str):
    """
    We use pip 3.8 executable to install tree_sitter wheel. Call with `check=True` to block until subprocess completes.
    """
    if find_spec("tree_sitter") is None:
        subprocess.run(
            [pip_path, "install", "--target", str(DEPS_PATH), f"tree_sitter=={TREE_SITTER_BINDINGS_VERSION}"],
            check=True,
        )


def clone_language(org_and_repo: str):
    _, repo = org_and_repo.split("/")
    subprocess.run(["git", "clone", f"https://github.com/{org_and_repo}", str(BUILD_PATH / repo)], check=True)


def get_so_file(language_name: str):
    return f"language-{language_name}.so"


def get_language_names_from_settings():
    return cast(List[str], get_settings().get("installed_languages"))


def clone_languages():
    """
    Clone language repos from which language `.so` files can be built.
    """
    language_names = get_language_names_from_settings()
    files = set(f for f in os.listdir(BUILD_PATH))
    for name in set(language_names):
        if name not in LANGUAGE_NAME_TO_REPO:
            log(f'"{name}" language is not supported, read more at {PROJECT_REPO}')
            continue

        org_and_repo = LANGUAGE_NAME_TO_REPO[name]
        _, repo = org_and_repo.split("/")
        if repo in files:
            # We've already cloned this repo
            continue

        log(f"installing {org_and_repo} repo for {name} language", with_status=True)
        clone_language(org_and_repo)
        files.add(repo)  # Avoid cloning a repo used for multiple languages multiple times


def build_languages():
    """
    Build missing language `.so` files for installed languages. We use python 3.8 executable to build languages,
    because the python bundled with Sublime can't do this.

    Note: `installed_languages` specified in `TreeSitter.sublime-settings`, `python` installed by default.
    """
    language_names = get_language_names_from_settings()
    python_path = cast(str, get_settings().get("python_path"))

    files = set(f for f in os.listdir(BUILD_PATH))
    for name in set(language_names):
        if (so_file := get_so_file(name)) in files:
            # We've already built this .so file
            continue

        if name not in LANGUAGE_NAME_TO_PATH:
            continue

        path = LANGUAGE_NAME_TO_PATH[name]
        log(f"building {name} language from files at {path}", with_status=True)
        subprocess.run(
            [
                python_path,
                str(PROJECT_ROOT / "src" / "build.py"),
                str(BUILD_PATH / path),
                str(BUILD_PATH / so_file),
            ],
            check=True,
        )


def instantiate_languages():
    """
    Instantiate `Language`s for language `.so` files, and put them in `SCOPE_TO_LANGUAGE`. This takes about 0.1ms for 2
    languages on my machine.
    """
    from tree_sitter import Language

    language_names = get_language_names_from_settings()
    files = set(f for f in os.listdir(BUILD_PATH))
    for name in set(language_names):
        if name not in LANGUAGE_NAME_TO_SCOPES:
            continue

        if (so_file := get_so_file(name)) not in files:
            continue

        # We've already instantiated this language, no need to do it again
        if all(scope in SCOPE_TO_LANGUAGE for scope in LANGUAGE_NAME_TO_SCOPES[name]):
            continue

        language = Language(str(BUILD_PATH / so_file), name)

        for scope in LANGUAGE_NAME_TO_SCOPES[name]:
            SCOPE_TO_LANGUAGE[scope] = language


#
# Code for caching syntax trees by their `buffer_id`s, and keeping them in sync as `TextChange`s occur
# https://www.sublimetext.com/docs/api_reference.html#sublime.View
#


def check_scope(scope: str | None):
    if not scope or scope not in SCOPE_TO_LANGUAGE:
        return None
    return scope


def byte_offset(point: int, s: str):
    """
    Convert a Sublime [Point](https://www.sublimetext.com/docs/api_reference.html#sublime.Point), the offset from the
    beginning of the buffer in UTF-8 code points, to a byte offset. For UTF-8, byte is the same as "code unit".

    Tree-sitter works with code unit offsets, not code point offsets. If source code is ASCII it makes no difference,
    but testing shows that making edits with code points instead of code units corrupts trees for non-ASCII source.

    ---

    Note that Sublime (confusingly) calls code points offsets "character" offsets. Multiple code points can result in
    just one user-perceived character, e.g. this one: שָׁ

    More info here: http://utf8everywhere.org/, https://tonsky.me/blog/unicode/
    """
    return len(s[:point].encode())


def get_edit(
    change: sublime.TextChange,
    s: str,
) -> tuple[int, int, int, tuple[int, int], tuple[int, int], tuple[int, int]]:
    """
    There are just two cases to handle:

    - Text inserted
    - Text deleted

    Sublime serializes text replacement as insertion then deletion.

    - For insertion, the start and end historic positions `a` and `b` are the same, and `str` contains the inserted text
    - For deletion, `str` is empty, `b` is where deletion starts, and `a` is where deletion ends, s.t. a < b

    `Tree.edit` [has the following signature](https://github.com/tree-sitter/py-tree-sitter#editing):

    ```py
    def edit(
        self,
        start_byte: int,
        old_end_byte: int,
        new_end_byte: int,
        start_point: tuple[int, int],
        old_end_point: tuple[int, int],
        new_end_point: tuple[int, int],
    ) -> None:
    ```
    """

    if change.str:
        # Insertion, where `a.pt` equals `b.pt`
        change_bytes = change.str.encode()

        start_byte = byte_offset(change.a.pt, s)
        old_end_byte = start_byte
        new_end_byte = start_byte + len(change_bytes)

        start_point = (change.a.row, change.a.col_utf8)
        old_end_point = (change.b.row, change.b.col_utf8)
        # https://docs.python.org/3/library/stdtypes.html#str.splitlines
        lines = change_bytes.splitlines()
        assert len(lines) > 0
        last_line = lines[-1]
        new_end_col = change.a.col_utf8 + len(last_line) if len(lines) == 1 else len(last_line)
        new_end_point = (change.a.row + len(lines) - 1, new_end_col)
    else:
        # Deletion, where `a.pt < b.pt`
        start_byte = byte_offset(change.a.pt, s)
        old_end_byte = start_byte + change.len_utf8
        new_end_byte = start_byte

        start_point = (change.a.row, change.a.col_utf8)
        old_end_point = (change.b.row, change.b.col_utf8)
        new_end_point = (change.a.row, change.a.col_utf8)

    return start_byte, old_end_byte, new_end_byte, start_point, old_end_point, new_end_point


def edit(parser: Parser, scope: ScopeType, changes: list[sublime.TextChange], tree: Tree, s: str) -> Tree:
    """
    To get the new tree, do `new_tree = parser.parse(new_source, tree)`

    Note that Sublime serializes text changes s.t. that they can be applied as is and in order, even if text is replaced
    and/or there are multiple selections.
    """
    parser.set_language(SCOPE_TO_LANGUAGE[scope])

    for change in changes:
        tree.edit(*get_edit(change, s))

    return parser.parse(s.encode(), tree)


def parse(parser: Parser, scope: ScopeType, s: str) -> Tree:
    """
    Note: the `set_language` call costs nothing, I can call it 2 million times a second on 2021 M1 MPB with 16gb RAM.
    """
    parser.set_language(SCOPE_TO_LANGUAGE[scope])
    return parser.parse(s.encode())


def make_tree_dict(tree: Tree, scope: ScopeType) -> TreeDict:
    return {"tree": tree, "updated_s": time.monotonic(), "scope": scope}


def get_scope(view: View) -> str | None:
    syntax = view.syntax()
    if not syntax:
        return None
    return syntax.scope


def publish_tree_update(window: sublime.Window | None, buffer_id: int, scope: str):
    """
    See `TreeSitterUpdateTreeCommand`.
    """
    if not window:
        return

    window.run_command(
        "tree_sitter_update_tree",
        {
            "buffer_id": buffer_id,
            "scope": scope or "",
        },
    )


def get_view_text(view: View):
    return view.substr(sublime.Region(0, view.size()))


def trim_cached_trees(size: int = MAX_CACHED_TREES):
    """
    Note that trimming an item is O(N) in `MAX_CACHED_TREES`.

    This is fast enough, and much easier than using heapq or similar to implement a sorted set.
    """
    while len(BUFFER_ID_TO_TREE) > MAX_CACHED_TREES:
        _, buffer_id = min((d["updated_s"], buffer_id) for buffer_id, d in BUFFER_ID_TO_TREE.items())
        BUFFER_ID_TO_TREE.pop(buffer_id, None)


def parse_view(parser: Parser, view: View, view_text: str, publish_update: bool = True):
    """
    Defined outside of `TreeSitterEventListener` so it can be called by anything, e.g. called on the active buffer after
    a new language is installed and loaded.
    """
    scope = get_scope(view)
    if not (scope := check_scope(scope)):
        return

    buffer_id = view.buffer().id()
    tree = parse(parser, scope, s=view_text)

    BUFFER_ID_TO_TREE[buffer_id] = make_tree_dict(tree, scope)

    if publish_update:
        publish_tree_update(view.window(), buffer_id=buffer_id, scope=scope)
    trim_cached_trees()


def install_languages():
    """
    - Clones language repos, and builds .so files on disk
    - Instantiates `Language` instance and adds it to `SCOPE_TO_LANGUAGE`

    Defined as a function so it can all be run in a thread on `plugin_loaded`.

    Idempotent. Also, doesn't reclone/rebuild/reinstantiate languages that have been cloned/built/instantiated.
    """
    from tree_sitter import Parser

    clone_languages()
    build_languages()
    instantiate_languages()
    if view := sublime.active_window().active_view():
        if view.buffer().id() not in BUFFER_ID_TO_TREE:
            parse_view(Parser(), view, get_view_text(view), publish_update=False)


def plugin_loaded():
    """
    Called after plugin is loaded (we can use functions like `sublime.load_settings`), but before events fired.

    We load any uncloned or unbuilt languages in the background, and if a language needed to parse the active view was
    just installed, we parse this view when we're finished.
    """
    log(f'Python bindings installed at "{DEPS_PATH}"')
    log(f'language repos and .so files installed at "{BUILD_PATH}"')

    settings = get_settings()

    python_path = cast(str, settings.get("python_path"))
    if not python_path:
        log("ERROR, `python_path` must be set")
        return

    pip_path = cast(str, settings.get("pip_path"))
    if not pip_path:
        head, _ = os.path.split(python_path)
        pip_path = str(Path(head) / "pip")

    install_tree_sitter(pip_path)
    instantiate_languages()
    Thread(target=install_languages).start()


class TreeSitterUpdateTreeCommand(sublime_plugin.WindowCommand):
    """
    So client code can "subscribe" to tree updates with an `EventListener`. See README for more info.
    """

    def run(self, **kwargs):
        pass


class TreeSitterEventListener(sublime_plugin.EventListener):
    """
    One of these for the whole Sublime instance.

    When a buffer is loaded, reverted, or reloaded, we do a full parse to get its tree, and cache that. This ensures the
    tree matches the buffer text even if this text is edited e.g. outside of ST.
    """

    @property
    def parser(self):
        """
        This is a lazy loading hack. We can't get settings, which means we can't ensure `tree_sitter` is installed,
        until the plugin is loaded and plugin classes have been instantiated.
        """
        if not hasattr(self, "_parser"):
            from tree_sitter import Parser

            self._parser = Parser()
        return self._parser

    def handle_load(self, view: View):
        s = get_view_text(view)

        def cb():
            parse_view(self.parser, view, s)

        sublime.set_timeout_async(callback=cb, delay=0)

    def on_activated(self, view: View):
        """
        Called when view gains focus. Ensures that we parse buffers on Sublime Text startup, where `on_load` callbacks
        not called. Testing shows that `on_text_changed` callbacks always enqueued after `on_activated` callbacks.
        """
        if view.buffer().id() not in BUFFER_ID_TO_TREE:
            self.handle_load(view)

    def on_load(self, view: View):
        """
        Testing suggests that `on_activated` always called before `on_load`. To be extra safe, we handle both of these
        events, and bail out if the other has already run for a given buffer.
        """
        if view.buffer().id() not in BUFFER_ID_TO_TREE:
            self.handle_load(view)

    def on_reload(self, view: View):
        self.handle_load(view)

    def on_revert(self, view: View):
        self.handle_load(view)


class TreeSitterTextChangeListener(sublime_plugin.TextChangeListener):
    """
    Under the hood, ST synchronously puts any async callbacks onto a queue. It asynchronously handles them in FIFO
    order in a separate thread. All async callbacks are handled by the same thread. Source code suggests this,
    testing with `time.sleep` confirms it. This ensures there are no races between "text change" events
    (almost always edit) and "load" (always parse).

    When a text change occurs, we get its buffer and its syntax, look up the tree and metadata, and update/create the
    tree as necessary. Every listener instance is bound to a buffer, so we know in which buffer text changes occur.
    """

    @property
    def parser(self):
        if not hasattr(self, "_parser"):
            from tree_sitter import Parser

            self._parser = Parser()
        return self._parser

    def on_text_changed(self, changes: list[sublime.TextChange]):
        view = self.buffer.primary_view()
        scope = get_scope(view)
        if not (scope := check_scope(scope)):
            return

        buffer_id = self.buffer.id()
        view_text = get_view_text(view)

        def cb():
            """
            Calling `get_view_text()` in `on_text_changed_async` doesn't always return view text right after the edit
            because it's async.

            So, we handle the text change event in the main UI thread, get the new view text right there, and queue up
            a "background job" with `set_timeout_async` to parse the new tree. This works because `set_timeout_async`
            uses the same queue as the other `_async` methods.
            """

            if buffer_id not in BUFFER_ID_TO_TREE:
                tree = parse(self.parser, scope, s=view_text)
            else:
                tree = edit(self.parser, scope, changes, BUFFER_ID_TO_TREE[buffer_id]["tree"], s=view_text)

            BUFFER_ID_TO_TREE[buffer_id] = make_tree_dict(tree, scope)
            publish_tree_update(view.window(), buffer_id=buffer_id, scope=scope)
            trim_cached_trees()

        sublime.set_timeout_async(callback=cb, delay=0)


#
# Maintenance commands, e.g. for installing, removing, and updating languages
#


def get_instantiated_language_names():
    return set(SCOPE_TO_LANGUAGE_NAME[scope] for scope in SCOPE_TO_LANGUAGE)


def remove_language(language: str):
    """
    - Remove language repo and .so file from disk
    - Remove `Language` instance from `SCOPE_TO_LANGUAGE`
    """
    org_and_repo = LANGUAGE_NAME_TO_REPO.get(language)
    if org_and_repo:
        _, repo = org_and_repo.split("/")
        try:
            rmtree(BUILD_PATH / repo)
        except Exception as e:
            log(f"error removing {repo} for {language}: {e}")

    so_file = get_so_file(language)
    try:
        os.remove(BUILD_PATH / so_file)
    except Exception as e:
        log(f"error removing {so_file} for {language}: {e}")

    for scope in LANGUAGE_NAME_TO_SCOPES.get(language, []):
        SCOPE_TO_LANGUAGE.pop(scope, None)


class TreeSitterSelectLanguageMixin:
    window: sublime.Window

    def run(self, **kwargs):
        """
        Allow user to select from installed and uninstalled languages in quick panel. Render language for the active
        view's scope as first option.
        """
        available_languages = sorted(list(LANGUAGE_NAME_TO_SCOPES.keys()))
        instantiated_languages = get_instantiated_language_names()

        view = self.window.active_view()
        scope = get_scope(view) if view else None
        active_language = SCOPE_TO_LANGUAGE_NAME[scope] if scope in SCOPE_TO_LANGUAGE_NAME else None

        if active_language in available_languages:
            idx = available_languages.index(active_language)
            available_languages.insert(0, available_languages.pop(idx))

        self.languages = available_languages

        def get_option(language: str):
            prefix = "✅" if language in instantiated_languages else "❌"
            return f"{prefix}        {language}"

        self.window.show_quick_panel([get_option(lang) for lang in self.languages], self.on_select)

    def on_select(self, idx: int):
        raise NotImplementedError


class TreeSitterInstallLanguageCommand(TreeSitterSelectLanguageMixin, sublime_plugin.WindowCommand):
    """
    - Add a language to `"installed_languages"` in settings
    - Install it with `install_languages`
    """

    def on_select(self, idx: int):
        if idx < 0:
            return

        language = self.languages[idx]

        settings = get_settings()
        languages = get_language_names_from_settings()
        if language not in languages:
            languages.append(language)

        settings.set("installed_languages", languages)
        sublime.save_settings(SETTINGS_FILENAME)
        Thread(target=install_languages).start()


class TreeSitterRemoveLanguageCommand(TreeSitterSelectLanguageMixin, sublime_plugin.WindowCommand):
    """
    - Remove a language from `"installed_languages"` in settings
    - Remove language from disk and from `SCOPE_TO_LANGUAGE`
    """

    def on_select(self, idx: int):
        if idx < 0:
            return

        language = self.languages[idx]

        settings = get_settings()
        languages = get_language_names_from_settings()
        while language in languages:
            languages.remove(language)

        settings.set("installed_languages", languages)
        sublime.save_settings(SETTINGS_FILENAME)
        Thread(target=lambda lang=language: remove_language(lang)).start()


class TreeSitterUpdateLanguageCommand(TreeSitterSelectLanguageMixin, sublime_plugin.WindowCommand):
    """
    - Remove language from disk and from `SCOPE_TO_LANGUAGE`, without changing settings
    - Reinstall it with `install_languages`
    """

    def on_select(self, idx: int):
        if idx < 0:
            return

        language = self.languages[idx]

        def remove_and_reinstall_language():
            remove_language(language)
            install_languages()

        Thread(target=remove_and_reinstall_language).start()
