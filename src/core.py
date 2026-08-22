"""
Ideally TreeSitter would be a "dependency", see https://packagecontrol.io/docs/dependencies, but dependencies
can't interface with `sublime_plugin` directly. This means no commands and no event listeners. See
https://github.com/SublimeText/sublime_lib/issues/127#issuecomment-516397027.

This means plugins that "depend" on this one, i.e. that do `from sublime_tree_sitter import get_tree_dict`, need to be
loaded after this one, or they need to do `sublime_tree_sitter` imports after this plugin has loaded.

TreeSitter does the following:

- Installs Tree-sitter Python bindings, see https://github.com/tree-sitter/py-tree-sitter
    - Importable by other plugins with `import tree_sitter`
- Instantiates Tree-sitter languages on demand from https://github.com/xberg-io/tree-sitter-language-pack, which
  downloads and caches precompiled parsers for ~370 languages, based on settings
- Provides APIs for a bunch of stuff (see more in README)

It's easy to build Tree-sitter plugins on top of this one, for "structural" editing, selection, navigation, code
folding, symbol maps… See e.g. https://zed.dev/blog/syntax-aware-editing for ideas. It's performant and doesn't block
the main thread.

`tree_sitter` and `tree_sitter_language_pack` aren't installed as Package Control "dependencies" yet (see README), so
they need to be installed with `uv sync`, run from this plugin's directory, before this plugin can do anything.

It has the following limitations:

- It doesn't support nested syntax trees, e.g. JS code in `<script>` tags in HTML docs
    - Ideas on how to do this: https://www.gnu.org/software/emacs/manual/html_node/elisp/Multiple-Languages.html
- It only supports source code encoded with ASCII / UTF-8 (Tree-sitter also supports UTF-16)
- Due to how syntax highlighting works in Sublime, it can't be used for syntax highlighting
    - See e.g. https://github.com/sublimehq/sublime_text/issues/817
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from threading import Thread
from typing import TYPE_CHECKING, TypedDict, cast

import sublime
import sublime_plugin
from sublime import View

from .utils import (
    DEPS_PATH,
    LIB_PATH,
    SETTINGS_FILENAME,
    ScopeType,
    SettingsDict,
    add_path,
    get_debug,
    get_file_ignore_patterns,
    get_language_name_to_debounce_ms,
    get_language_name_to_scopes,
    get_scope_to_language_name,
    get_settings,
    get_settings_dict,
    log,
)

if TYPE_CHECKING:
    from tree_sitter import Language, Parser, Tree


@dataclass
class MutableSettings:
    d: SettingsDict | None


# Don't put this var in `utils.py`
mutable_settings = MutableSettings(d=None)


PROJECT_REPO = "https://github.com/sublime-treesitter/TreeSitter"

MAX_CACHED_TREES = 16
SCOPE_TO_LANGUAGE: dict[ScopeType, Language] = {}

# LRU cache, dict of `(buffer_id, syntax)` tuple keys pointing to dict with tree instance and other metadata.
BUFFER_ID_TO_TREE: dict[int, TreeDict] = {}

# These need to be added to plugin host's `sys.path` before other plugins that depend on them load, and before
# `tree_sitter`/`tree_sitter_language_pack` are imported
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


def on_update_settings():
    """
    Reinstantiate languages in case `installed_languages` setting updated.

    If there's an easier way to check whether plugin settings have changed I'd love to know what it is!
    """
    settings_dict = get_settings_dict()
    previous_settings_dict = mutable_settings.d
    mutable_settings.d = settings_dict

    if previous_settings_dict is not None:
        installed_languages_changed = set(previous_settings_dict.get("installed_languages")) != set(
            settings_dict.get("installed_languages")
        )
        if installed_languages_changed:
            Thread(target=install_languages).start()


def on_load():
    """
    Called in `plugin_loaded` in `load.py`. Called after plugin is loaded (we can use functions like
    `sublime.load_settings`), but before events fired.

    We load any uninstantiated languages in the background, and if a language needed to parse the active view was just
    installed, we parse this view when we're finished.
    """
    settings = get_settings()
    mutable_settings.d = cast(SettingsDict, settings.to_dict())
    settings.clear_on_change("TreeSitter")
    settings.add_on_change("TreeSitter", on_update_settings)

    # Only instantiate languages already downloaded and cached by `tree_sitter_language_pack` on a previous run, so
    # plugin load never blocks the main thread on network I/O. `install_languages`, run in a background thread right
    # after, downloads and instantiates anything still missing.
    instantiate_languages(only_downloaded=True)
    Thread(target=install_languages).start()


def instantiate_languages(only_downloaded: bool = False):
    """
    Instantiate `Language`s with `tree_sitter_language_pack`, and put them in `SCOPE_TO_LANGUAGE`. `get_language`
    downloads and caches a language's precompiled parser the first time it's called, so this can block on network I/O;
    pass `only_downloaded=True` to skip languages not already cached on disk, e.g. when calling from the main thread.
    """
    try:
        from tree_sitter_language_pack import downloaded_languages, get_language
    except ImportError:
        log(
            "`tree_sitter_language_pack` isn't installed: run `uv sync` from this plugin's directory, then run "
            "`TreeSitter: Reload Plugin`",
            with_status=True,
        )
        return

    settings_dict = get_settings_dict()
    language_names = settings_dict["installed_languages"]
    language_name_to_scopes = get_language_name_to_scopes(mutable_settings.d)
    cached_language_names = set(downloaded_languages()) if only_downloaded else None

    for name in set(language_names):
        if name not in language_name_to_scopes:
            continue

        # We've already instantiated this language, no need to do it again
        if all(scope in SCOPE_TO_LANGUAGE for scope in language_name_to_scopes[name]):
            continue

        if cached_language_names is not None and name not in cached_language_names:
            continue

        try:
            language = get_language(name)
        except Exception as e:
            log(f'"{name}" language not available from tree_sitter_language_pack, read more at {PROJECT_REPO}: {e}')
            continue

        for scope in language_name_to_scopes[name]:
            SCOPE_TO_LANGUAGE[scope] = language


#
# Code for caching syntax trees by their `buffer_id`s, and keeping them in sync as `TextChange`s occur
# https://www.sublimetext.com/docs/api_reference.html#sublime.View
#


def check_scope(scope: str | None):
    """
    Ensure scope is a supported scope. If scope doesn't match supported scopes, try to return any supported scope that's
    a prefix of scope.

    This means the Tree-sitter parser for `source.yaml` can be used for any scope that starts with `source.yaml`, e.g.
    `source.yaml.sublime.syntax`.
    """
    if not scope:
        return None
    if scope in SCOPE_TO_LANGUAGE:
        return scope

    scopes = list(SCOPE_TO_LANGUAGE.keys())
    for supported_scope in scopes:
        if scope.startswith(f"{supported_scope}."):
            return supported_scope


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
    parser.language = SCOPE_TO_LANGUAGE[scope]

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
    Note: setting `parser.language` costs nothing, I can do it 2 million times a second on 2021 M1 MPB with 16gb RAM.
    """
    parser.language = SCOPE_TO_LANGUAGE[scope]
    return parser.parse(s.encode())


def make_tree_dict(tree: Tree, s: str, scope: ScopeType) -> TreeDict:
    return {"tree": tree, "s": s, "updated_s": time.monotonic(), "scope": scope}


def get_scope(view: View) -> str | None:
    if (file_ignore_patterns := get_file_ignore_patterns(mutable_settings.d)) and (file_path := view.file_name()):
        for pattern in file_ignore_patterns:
            if re.search(pattern, file_path):
                return None

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
    Downloads (if needed) and instantiates `Language`s for `installed_languages`, and adds them to `SCOPE_TO_LANGUAGE`.

    Defined as a function so it can all be run in a thread on `plugin_loaded`.

    Idempotent. Also, doesn't redownload/reinstantiate languages that have already been downloaded/instantiated.
    """
    from tree_sitter import Parser

    instantiate_languages()
    if (view := sublime.active_window().active_view()) and view.buffer().id() not in BUFFER_ID_TO_TREE:
        parse_view(Parser(), view, get_view_text(view), publish_update=False)


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
        self.debug = get_debug(mutable_settings.d)
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
            scope_to_language_name = get_scope_to_language_name(mutable_settings.d)
            language_name_to_debounce_ms = get_language_name_to_debounce_ms(mutable_settings.d)
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
    return set(get_scope_to_language_name(mutable_settings.d)[scope] for scope in SCOPE_TO_LANGUAGE)


def remove_language(language: str):
    """
    Remove `Language` instance from `SCOPE_TO_LANGUAGE`. Doesn't touch `tree_sitter_language_pack`'s on-disk cache,
    which is shared across all languages and safe to leave in place.
    """
    for scope in get_language_name_to_scopes(mutable_settings.d).get(language, []):
        SCOPE_TO_LANGUAGE.pop(scope, None)


class TreeSitterSelectLanguageMixin:
    window: sublime.Window

    def run(self, **kwargs):
        """
        Allow user to select from installed and uninstalled languages in quick panel. Render language for the active
        view's scope as first option.
        """
        available_languages = sorted(list(get_language_name_to_scopes(mutable_settings.d).keys()))
        instantiated_languages = get_instantiated_language_names()

        view = self.window.active_view()
        scope = get_scope(view) if view else None
        scope_to_language_name = get_scope_to_language_name(mutable_settings.d)
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
    - Remove language from `SCOPE_TO_LANGUAGE`
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
