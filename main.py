"""
Ideally TreeSitter would be a "dependency", see https://packagecontrol.io/docs/dependencies, but dependencies
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
    - Querying a tree or node, walking a tree or node
    - Getting a node from a point or selection, getting a region from a node

It's easy to build Tree-sitter plugins on top of this one, for "structural" editing, selection, navigation, code
folding, symbol maps… See e.g. https://zed.dev/blog/syntax-aware-editing for ideas. It's performant and doesn't block
the main thread.

It has the following limitations:

- It doesn't support nested syntax trees, e.g. JS code in `<script>` tags in HTML docs
    - Ideas on how to do this: https://www.gnu.org/software/emacs/manual/html_node/elisp/Multiple-Languages.html
- It only supports source code encoded with ASCII / UTF-8 (Tree-sitter also supports UTF-16)
- Due to limitations in Sublime's bundled Python, it requires an external Python 3.8 executable (see settings)
    - Calling `build_library` raises e.g. `ModuleNotFoundError: No module named '_sysconfigdata__darwin_darwin'`
    - This module is built dynamically, and doesn't exist in Python bundled with Sublime Text
    - Alternative is pre-compile and vendor .so files, e.g. `build/language-python.so`, for all platforms and languages
- Due to how syntax highlighting works in Sublime, it can't be used for syntax highlighting
    - See e.g. https://github.com/sublimehq/sublime_text/issues/817
- It breaks if the package is reloaded, which is a nuisance if you're working on this codebase
    - After package is reloaded, `BUFFER_ID_TO_TREE` is no longer updated on buffer changes
"""

from __future__ import annotations

import os
import subprocess
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from shutil import rmtree
from threading import Thread
from typing import TYPE_CHECKING, Any, TypedDict, cast

import sublime
import sublime_plugin
from sublime import View

from .src.utils import (
    BUILD_PATH,
    DEPS_PATH,
    LIB_PATH,
    PROJECT_ROOT,
    SETTINGS_FILENAME,
    ScopeType,
    add_path,
    get_language_name_to_debounce_ms,
    get_language_name_to_org_and_repo,
    get_language_name_to_parser_path,
    get_language_name_to_scopes,
    get_scope_to_language_name,
    get_settings,
    get_settings_dict,
    log,
    maybe_none,
    not_none,
)

if TYPE_CHECKING:
    from tree_sitter import Language, Node, Parser, Tree

TREE_SITTER_BINDINGS_VERSION = "0.20.2"
PROJECT_REPO = "https://github.com/sublime-treesitter/TreeSitter"

MAX_CACHED_TREES = 16
SCOPE_TO_LANGUAGE: dict[ScopeType, Language] = {}

# LRU cache, dict of `(buffer_id, syntax)` tuple keys pointing to dict with tree instance and other metadata.
BUFFER_ID_TO_TREE: dict[int, TreeDict] = {}

# These need to be added to plugin host's `sys.path` before other plugins that depend on them load
add_path(str(LIB_PATH))
add_path(str(DEPS_PATH))


class TreeDict(TypedDict):
    tree: Tree
    s: str
    scope: ScopeType
    updated_s: float


#
# Code for installing tree sitter, and installing/building languages
#


def install_tree_sitter(pip_path: str):
    """
    We use pip 3.8 executable to install tree_sitter wheel. Call with `check=True` to block until subprocess completes.
    """
    try:
        v = version("tree_sitter")
    except PackageNotFoundError:
        v = ""

    if v != TREE_SITTER_BINDINGS_VERSION:
        # Bindings either not installed, or correct version not installed
        log(f"installing tree_sitter=={TREE_SITTER_BINDINGS_VERSION}")
        subprocess.run(
            [pip_path, "install", "--target", str(DEPS_PATH), f"tree_sitter=={TREE_SITTER_BINDINGS_VERSION}"],
            check=True,
        )


def clone_language(org_and_repo: str):
    _, repo = org_and_repo.split("/")
    subprocess.run(["git", "clone", f"https://github.com/{org_and_repo}", str(BUILD_PATH / repo)], check=True)


def get_so_file(language_name: str):
    return f"language-{language_name}.so"


def clone_languages():
    """
    Clone language repos from which language `.so` files can be built.
    """
    language_names = get_settings_dict()["installed_languages"]
    files = set(f for f in os.listdir(BUILD_PATH))
    language_name_to_org_and_repo = get_language_name_to_org_and_repo()

    for name in set(language_names):
        if name not in language_name_to_org_and_repo:
            log(f'"{name}" language is not supported, read more at {PROJECT_REPO}')
            continue

        org_and_repo = language_name_to_org_and_repo[name]
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
    settings_dict = get_settings_dict()
    language_names = settings_dict["installed_languages"]
    python_path = settings_dict["python_path"]

    files = set(f for f in os.listdir(BUILD_PATH))
    language_name_to_parser_path = get_language_name_to_parser_path()

    for name in set(language_names):
        if (so_file := get_so_file(name)) in files:
            # We've already built this .so file
            continue

        if name not in language_name_to_parser_path:
            continue

        path = language_name_to_parser_path[name]
        log(f"building {name} language from files at {path}", with_status=True)
        subprocess.run(
            [
                os.path.expanduser(python_path),
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

    language_names = get_settings_dict()["installed_languages"]
    files = set(f for f in os.listdir(BUILD_PATH))
    language_name_to_scopes = get_language_name_to_scopes()

    for name in set(language_names):
        if name not in language_name_to_scopes:
            continue

        if (so_file := get_so_file(name)) not in files:
            continue

        # We've already instantiated this language, no need to do it again
        if all(scope in SCOPE_TO_LANGUAGE for scope in language_name_to_scopes[name]):
            continue

        language = Language(str(BUILD_PATH / so_file), name)

        for scope in language_name_to_scopes[name]:
            SCOPE_TO_LANGUAGE[scope] = language


#
# Code for caching syntax trees by their `buffer_id`s, and keeping them in sync as `TextChange`s occur
# https://www.sublimetext.com/docs/api_reference.html#sublime.View
#


def check_scope(scope: str | None):
    if not scope or scope not in SCOPE_TO_LANGUAGE:
        return None
    return scope


def get_edit(
    change: sublime.TextChange,
    s: str,
    should_change_s: bool,
) -> tuple[tuple[int, int, int, tuple[int, int], tuple[int, int], tuple[int, int]], str]:
    """
    Args:

    - `s`: Buffer text before text change was applied
    - `change`: TextChange
    - `should_change_s`: Should we change s? Not doing so for final TextChange in "group" is a performance optimization

    Returns:

    - Tuple with updated `s` after change applied, and `Tree.edit` args

    ---

    There are two cases to handle:

    - Text inserted
    - Text deleted

    - For insertion, the start and end historic positions `a` and `b` are the same, and `str` is the inserted text
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
    ):
    ```

    References:

    - https://github.com/tree-sitter/tree-sitter/issues/1792
    - https://github.com/tree-sitter/tree-sitter/issues/210
    """
    changed_s = s

    # Initialize variables assuming neither insertion nor deletion
    start_byte = byte_offset(change.a.pt, s)
    old_end_byte = start_byte
    new_end_byte = start_byte

    start_point = (change.a.row, change.a.col_utf8)
    old_end_point = (change.b.row, change.b.col_utf8)
    new_end_point = (change.a.row, change.a.col_utf8)

    if change.a.pt < change.b.pt:
        # Deletion
        old_end_byte = start_byte + change.len_utf8
        if should_change_s:
            changed_s = changed_s[: change.a.pt] + changed_s[change.b.pt :]

    if change.str:
        # Insertion, note that `start_byte`, `old_end_byte`, `start_point`, and `old_end_point` have already been set
        change_bytes = change.str.encode()
        new_end_byte = start_byte + len(change_bytes)

        lines = change_bytes.splitlines()
        last_line = lines[-1]
        new_end_col = change.a.col_utf8 + len(last_line) if len(lines) == 1 else len(last_line)
        new_end_point = (change.a.row + len(lines) - 1, new_end_col)
        if should_change_s:
            changed_s = changed_s[: change.a.pt] + change.str + changed_s[change.a.pt :]

    return (start_byte, old_end_byte, new_end_byte, start_point, old_end_point, new_end_point), changed_s


def edit(
    parser: Parser,
    scope: ScopeType,
    changes: list[sublime.TextChange],
    tree: Tree,
    s: str,
    new_s: str,
    debug: bool = False,
) -> Tree:
    """
    To get the new tree, do `new_tree = parser.parse(new_s, tree)`

    Note that Sublime serializes text changes s.t. that they can be applied as is and in order, even if text is replaced
    and/or there are multiple selections.
    """
    parser.set_language(SCOPE_TO_LANGUAGE[scope])

    changed_s = s
    for idx, change in enumerate(changes):
        should_change_s = debug or idx < len(changes) - 1  # Performance optimization, see `get_edit`
        edit_tuple, changed_s = get_edit(change, changed_s, should_change_s)
        tree.edit(*edit_tuple)

    if debug:
        # Applying changes to `s` must yield `new_s`
        assert changed_s == new_s
    return parser.parse(new_s.encode(), tree)


def parse(parser: Parser, scope: ScopeType, s: str) -> Tree:
    """
    Note: the `set_language` call costs nothing, I can call it 2 million times a second on 2021 M1 MPB with 16gb RAM.
    """
    parser.set_language(SCOPE_TO_LANGUAGE[scope])
    return parser.parse(s.encode())


def make_tree_dict(tree: Tree, s: str, scope: ScopeType) -> TreeDict:
    return {"tree": tree, "s": s, "updated_s": time.monotonic(), "scope": scope}


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

    BUFFER_ID_TO_TREE[buffer_id] = make_tree_dict(tree, view_text, scope)
    trim_cached_trees()

    if publish_update:
        publish_tree_update(view.window(), buffer_id=buffer_id, scope=scope)

    return tree


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

    settings_dict = get_settings_dict()

    python_path = settings_dict["python_path"]
    if not python_path:
        log("ERROR, `python_path` must be set")
        return

    pip_path = settings_dict["pip_path"]
    if not pip_path:
        head, _ = os.path.split(python_path)
        pip_path = str(Path(head) / "pip")

    install_tree_sitter(os.path.expanduser(pip_path))
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

    It would be nice to (re)parse buffer `on_post_text_command` for "set_file_type" (syntax changes), but text commands
    run from command the palette aren't caught by event listeners.

    [This is a serious bug in the plugin API](https://github.com/sublimehq/sublime_text/issues/2234). Our workaround is
    to ensure client code can only access trees through `get_tree_dict`, which handles syntax changes on read.
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

    def on_close(self, view: View):
        """
        Called when a view is closed. If there are no other views into view's buffer, stop tracking buffer, because
        buffer is "dead". This way clients don't accidentally use them.
        """
        if not view.clones():
            BUFFER_ID_TO_TREE.pop(view.buffer().id(), None)

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
    Under the hood, ST synchronously puts async callbacks onto a queue. It asynchronously handles them in FIFO order in
    a separate thread. All async callbacks are handled by the same thread. Sublime source code suggests this, testing
    with `time.sleep` confirms it. This ensures there are no races between "text change" events (almost always edit)
    and "load" (always parse).

    When a text change occurs, we get its buffer and its syntax, look up the tree and metadata, and update/create the
    tree as necessary. Every listener instance is bound to a buffer, so we know in which buffer text changes occur.
    """

    def __init__(self, *args, **kwargs):
        self.debounce_ms: int | None = None
        self.last_text_changed_s = 0
        self.debug: bool = get_settings_dict().get("debug") or False
        super().__init__(*args, **kwargs)

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

        if self.debounce_ms is None:
            scope_to_language_name = get_scope_to_language_name()
            language_name_to_debounce_ms = get_language_name_to_debounce_ms()
            self.debounce_ms = round(language_name_to_debounce_ms.get(scope_to_language_name[scope], 0))

        buffer_id = self.buffer.id()
        view_text = get_view_text(view)

        self.last_text_changed_s = time.monotonic()
        debounce_ms = self.debounce_ms or 0

        def cb():
            """
            Calling `get_view_text()` in `on_text_changed_async` doesn't always return view text right after the edit
            because it's async.

            So, we handle the text change event in the main UI thread, get the new view text right there, and queue up
            a "background job" with `set_timeout_async` to parse the new tree. This works because `set_timeout_async`
            uses the same queue as the other `_async` methods.

            Note that some language parsers are so slow they visibly affect UI thread performance. Setting a
            `debounce_ms` for these languages is recommended.
            """
            tree_dict = BUFFER_ID_TO_TREE.get(buffer_id)
            scope_changed = bool(tree_dict) and tree_dict["scope"] != scope

            if not tree_dict or debounce_ms > 0 or scope_changed:
                dt_s = (time.monotonic() - self.last_text_changed_s) * 1000
                if dt_s < debounce_ms:
                    return
                tree = parse(self.parser, scope, s=view_text)
            else:
                tree = edit(
                    self.parser,
                    scope,
                    changes,
                    tree_dict["tree"],
                    s=tree_dict["s"],
                    new_s=view_text,
                    debug=self.debug,
                )

            BUFFER_ID_TO_TREE[buffer_id] = make_tree_dict(tree, view_text, scope)
            trim_cached_trees()
            publish_tree_update(view.window(), buffer_id=buffer_id, scope=scope)

        sublime.set_timeout_async(callback=cb, delay=debounce_ms + 1 if debounce_ms > 0 else 0)


#
# Maintenance commands, e.g. for installing, removing, and updating languages
#


def get_instantiated_language_names():
    return set(get_scope_to_language_name()[scope] for scope in SCOPE_TO_LANGUAGE)


def remove_language(language: str):
    """
    - Remove language repo and .so file from disk
    - Remove `Language` instance from `SCOPE_TO_LANGUAGE`
    """
    org_and_repo = get_language_name_to_org_and_repo().get(language)
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

    for scope in get_language_name_to_scopes().get(language, []):
        SCOPE_TO_LANGUAGE.pop(scope, None)


class TreeSitterSelectLanguageMixin:
    window: sublime.Window

    def run(self, **kwargs):
        """
        Allow user to select from installed and uninstalled languages in quick panel. Render language for the active
        view's scope as first option.
        """
        available_languages = sorted(list(get_language_name_to_scopes().keys()))
        instantiated_languages = get_instantiated_language_names()

        view = self.window.active_view()
        scope = get_scope(view) if view else None
        scope_to_language_name = get_scope_to_language_name()
        active_language = scope_to_language_name[scope] if scope in scope_to_language_name else None

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
        languages = get_settings_dict()["installed_languages"]
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
        languages = get_settings_dict()["installed_languages"]
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


#
# Public-facing API functions, and some helper functions
#


def get_tree_dict(buffer_id: int):
    """
    Get tree dict being maintained for this buffer, or instantiate new tree dict on the fly.
    """
    if not isinstance(cast(Any, buffer_id), int) or not (view := get_view_from_buffer_id(buffer_id)):
        return
    if not (scope := get_scope(view)) or not (scope := check_scope(scope)):
        BUFFER_ID_TO_TREE.pop(buffer_id, None)
        return

    tree_dict = BUFFER_ID_TO_TREE.get(buffer_id)
    if not tree_dict or tree_dict["scope"] != scope:
        from tree_sitter import Parser

        view_text = get_view_text(view)
        BUFFER_ID_TO_TREE[buffer_id] = make_tree_dict(parse(Parser(), scope, view_text), view_text, scope)
        trim_cached_trees()
        publish_tree_update(view.window(), buffer_id=buffer_id, scope=scope)

    return BUFFER_ID_TO_TREE.get(buffer_id)


def get_view_from_buffer_id(buffer_id: int) -> sublime.View | None:
    """
    Utilify function. Ensures `None` returned if a "dead" buffer id passed.
    """
    buffer = sublime.Buffer(buffer_id)
    view = buffer.primary_view()
    return view if maybe_none(view.id()) is not None else None


def get_tree_from_code(scope: ScopeType, s: str | bytes):
    """
    Get a syntax tree back for source code `s`.
    """
    from tree_sitter import Parser

    if scope not in SCOPE_TO_LANGUAGE:
        return None
    parser = Parser()
    parser.set_language(SCOPE_TO_LANGUAGE[scope])
    return parser.parse(s.encode() if isinstance(s, str) else s)


def query_tree(scope: ScopeType, query_s: str, tree_or_node: Tree | Node):
    """
    Query a syntax tree or node with `query_s`.

    See https://github.com/tree-sitter/py-tree-sitter#pattern-matching
    """
    from tree_sitter import Tree

    if scope not in SCOPE_TO_LANGUAGE:
        return None
    language = SCOPE_TO_LANGUAGE[scope]
    query = language.query(query_s)
    return query.captures(tree_or_node.root_node if isinstance(tree_or_node, Tree) else tree_or_node)


def walk_tree(tree_or_node: Tree | Node):
    """
    Walk all the nodes under `tree_or_node`.

    See https://github.com/tree-sitter/py-tree-sitter/issues/33#issuecomment-864557166
    """
    cursor = tree_or_node.walk()
    depth = 0

    reached_root = False
    while not reached_root:
        yield cursor.node, depth

        if cursor.goto_first_child():
            depth += 1
            continue

        if cursor.goto_next_sibling():
            continue

        retracing = True
        while retracing:
            if not cursor.goto_parent():
                retracing = False
                reached_root = True

            depth -= 1
            if cursor.goto_next_sibling():
                retracing = False


def descendant_for_byte_range(node: Node, start_byte: int, end_byte: int) -> Node | None:
    """
    Get the smallest node within the given byte range.

    This API added in September 2023: https://github.com/tree-sitter/py-tree-sitter/pull/150/files

    See also: https://tree-sitter.github.io/tree-sitter/using-parsers#named-vs-anonymous-nodes
    """
    return node.descendant_for_byte_range(start_byte, end_byte)  # type: ignore


def get_ancestors(node: Node) -> list[Node]:
    """
    Get all ancestors of node, including node itself.
    """
    nodes: list[Node] = []
    current_node: Node | None = node

    while current_node:
        nodes.append(current_node)
        current_node = current_node.parent
    return nodes


def get_node_spanning_region(region: sublime.Region | tuple[int, int], buffer_id: int) -> Node | None:
    """
    Get smallest node spanning region, s.t. node's start point is less than or equal to region's start point, and
    node's end point is greater than or equal region's end point.

    If there are two nodes matching a zero-width region, prefer the "deeper" of the two, i.e. the one furthest from the
    root of the tree.
    """
    if not (tree_dict := get_tree_dict(buffer_id)):
        return None

    region = region if isinstance(region, sublime.Region) else sublime.Region(*region)
    root_node = tree_dict["tree"].root_node
    s = tree_dict["s"]

    desc = descendant_for_byte_range(root_node, byte_offset(region.begin(), s), byte_offset(region.end(), s))

    if len(region) == 0:
        other_desc = descendant_for_byte_range(
            root_node,
            byte_offset(region.begin() - 1, s),
            byte_offset(region.end() - 1, s),
        )
    else:
        return desc

    if desc and other_desc:
        # If there are two nodes that match this region, prefer the "deeper" of the two
        return desc if len(get_ancestors(desc)) >= len(get_ancestors(other_desc)) else other_desc


def get_region_from_node(node: Node, buffer_id_or_view: int | sublime.View, reverse=False) -> sublime.Region:
    """
    Get `sublime.Region` that exactly spans `node`, for specified `buffer_id_or_view`.

    See [View.text_point_utf8](https://www.sublimetext.com/docs/api_reference.html#sublime.View.text_point_utf8).
    """
    view = get_view_from_buffer_id(buffer_id_or_view) if isinstance(buffer_id_or_view, int) else buffer_id_or_view

    if view is None:
        raise RuntimeError(f"Tree-sitter: {buffer_id_or_view} does not exist")

    p_a = view.text_point_utf8(*node.start_point)
    p_b = view.text_point_utf8(*node.end_point)
    return sublime.Region(a=p_a if not reverse else p_b, b=p_b if not reverse else p_a)


def get_size(node: Node) -> int:
    """
    Get size of node in bytes.
    """
    return node.end_byte - node.start_byte


def get_larger_ancestor(node: Node) -> Node | None:
    """
    Get "first" ancestor of node that's larger than this node.
    """
    while True:
        if not node.parent:
            return None
        if get_size(node.parent) > get_size(node):
            return node.parent
        node = node.parent


def get_ancestor(region: sublime.Region, view: sublime.View) -> Node | None:
    """
    Useful for e.g. expanding selection. Works as follows:

    - First, get smallest node spanning `region`
    - If this node's region is larger than `region`, return node
    - Else, get "first" ancestor of this node that's larger than this node
    """
    node = get_node_spanning_region(region, view.buffer_id())

    if not node or not node.parent:
        return None

    new_region = get_region_from_node(node, view)
    if len(new_region) > len(region):
        return node

    return get_larger_ancestor(node) or node.parent


def byte_offset(point: int, s: str):
    """
    Convert a Sublime [Point](https://www.sublimetext.com/docs/api_reference.html#sublime.Point), the offset from the
    beginning of the buffer in UTF-8 code points, to a byte offset. For UTF-8, byte is the same as "code unit".

    Tree-sitter works with code unit offsets, not code point offsets. If source code is ASCII this makes no difference,
    but testing shows that making edits with code points instead of code units corrupts trees for non-ASCII source.

    ---

    Note that Sublime (confusingly) calls code points offsets "character" offsets. Multiple code points can result in
    just one user-perceived character, e.g. this one: שָׁ

    More info here: http://utf8everywhere.org/, https://tonsky.me/blog/unicode/
    """
    return len(s[:point].encode())


def get_view_name(view: sublime.View):
    if name := view.file_name():
        return os.path.basename(name)

    return view.name() or ""


def scroll_to_region(region: sublime.Region, view: sublime.View):
    if region.b not in view.visible_region():
        view.show(region.b)


def get_descendant(region: sublime.Region, view: sublime.View) -> Node | None:
    """
    Find node that spans region, then find first descendant that's smaller than this node. This descendant is basically
    guaranteed to have at least one sibling.
    """
    if not (tree_dict := get_tree_dict(view.buffer_id())):
        return

    node = get_node_spanning_region(region, view.buffer_id()) or tree_dict["tree"].root_node
    for desc, _ in walk_tree(node):
        if get_size(desc) < get_size(node):
            return desc


def get_sibling(region: sublime.Region, view: sublime.View, forward: bool = True) -> Node | None:
    """
    - Find that node spans region
    - Find "first" ancestor of this node, including node itself, that has siblings
        - If node spanning region is root node, find "first" descendant that has siblings
    - Return the next or previous sibling
    """
    node = get_node_spanning_region(region, view.buffer_id())
    if not node:
        return

    if not node.parent:
        # We're at root node, so we find the first descendant that has siblings, and return sibling adjacent to region
        tree_dict = get_tree_dict(view.buffer_id())
        first_sibling = get_descendant(region, view)

        if first_sibling and first_sibling.parent and tree_dict:
            begin = byte_offset(region.begin(), tree_dict["s"])
            if forward:
                for sibling in first_sibling.parent.children:
                    if begin <= sibling.start_byte:
                        return sibling
            else:
                for sibling in reversed(first_sibling.parent.children):
                    if begin >= sibling.start_byte:
                        return sibling

        return first_sibling

    while node.parent and node.parent.parent:
        if len(node.parent.children) == 1:
            node = node.parent
        else:
            break

    siblings = not_none(node.parent).children
    idx = siblings.index(node)
    idx = idx + 1 if forward else idx - 1
    return siblings[idx % len(siblings)]


#
# Select commands, and print tree command for debugging
#


class TreeSitterSelectAncestorCommand(sublime_plugin.TextCommand):
    """
    Expand selection to smallest ancestor that's bigger than node spanning currently selected region.

    Does not select region corresponding to root node, i.e. region that spans entire buffer, because there are easier
    ways to do this…
    """

    def run(self, edit, reverse_sel: bool = True):
        sel = self.view.sel()
        new_region: sublime.Region | None = None

        for region in sel:
            new_node = get_ancestor(region, self.view)
            if new_node and new_node.parent:
                new_region = get_region_from_node(new_node, self.view, reverse=reverse_sel)
                self.view.sel().add(new_region)

        if new_region and len(sel) == 1:
            scroll_to_region(new_region, self.view)


class TreeSitterSelectSiblingCommand(sublime_plugin.TextCommand):
    """
    Find node spanning selected region, then find its next or previous sibling (depending on value of `forward`), and
    select this region or extend current selection (depending on value of `extend`).
    """

    def run(self, edit, forward: bool = True, extend: bool = False, reverse_sel: bool = True):
        sel = self.view.sel()
        new_regions: list[sublime.Region] = []

        for region in sel:
            if sibling := get_sibling(region, self.view, forward):
                new_region = get_region_from_node(sibling, self.view, reverse=reverse_sel)
                new_regions.append(new_region)
                if not extend:
                    sel.subtract(region)
                sel.add(new_region)

        if new_regions:
            scroll_to_region(new_regions[-1] if forward else new_regions[0], self.view)


class TreeSitterSelectDescendantCommand(sublime_plugin.TextCommand):
    """
    Find node that spans region, then find first descendant that's smaller than this node, and select region
    corresponding to thsi node.
    """

    def run(self, edit, reverse_sel: bool = True):
        sel = self.view.sel()
        new_region: sublime.Region | None = None

        for region in sel:
            if desc := get_descendant(region, self.view):
                new_region = get_region_from_node(desc, self.view, reverse=reverse_sel)
                sel.subtract(region)
                sel.add(new_region)

        if new_region and len(sel) == 1:
            scroll_to_region(new_region, self.view)


class TreeSitterPrintTreeCommand(sublime_plugin.TextCommand):
    """
    For debugging. If nothing selected, print syntax tree for buffer. Else, print segment(s) of tree for selection(s).
    """

    def format_node(self, node: Node):
        return f"{node.type}  {node.start_point} → {node.end_point}"

    def run(self, edit):
        indent = " " * 2
        tree_dict = get_tree_dict(self.view.buffer_id())
        if not tree_dict:
            return

        window = self.view.window()
        if not window:
            return

        root_nodes: list[Node] = []
        for region in self.view.sel():
            if len(region) > 0:
                root_node = get_node_spanning_region(region, self.view.buffer_id())
                if root_node:
                    root_nodes.append(root_node)

        if not root_nodes:
            root_nodes = [tree_dict["tree"].root_node]

        parts: list[str] = []
        for root_node in root_nodes:
            parts.extend([f"{indent * depth}{self.format_node(node)}" for node, depth in walk_tree(root_node)])
            parts.append("")

        name = get_view_name(self.view)
        view = window.new_file()
        view.set_name(f"Syntax Tree - {name}" if name else "Syntax Tree")
        view.set_scratch(True)
        view.insert(edit, 0, "\n".join(parts))
