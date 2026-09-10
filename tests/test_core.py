from __future__ import annotations

import types

from tree_sitter import Parser

from src import core


def test_instantiate_languages_populates_scope_to_language():
    # Populated by the session-scoped `_instantiate_languages` fixture
    assert "source.python" in core.SCOPE_TO_LANGUAGE
    assert "source.js" in core.SCOPE_TO_LANGUAGE


def test_instantiate_languages_downloads_scope_less_language(monkeypatch):
    """
    Regression test: `instantiate_languages` used to skip any name in `installed_languages` that had no entry in
    `SCOPE_TO_LANGUAGE_NAME`, so "installing" one of the many `tree_sitter_language_pack` languages this plugin
    doesn't map a scope to was a silent no-op - nothing was ever downloaded. It should still fetch/cache the parser,
    just without adding anything to `SCOPE_TO_LANGUAGE` (there's no scope to key it by).
    """
    import tree_sitter_language_pack as tslp

    requested: list[str] = []
    monkeypatch.setattr(tslp, "get_language", lambda name: requested.append(name) or object())
    monkeypatch.setattr(core, "get_settings_dict", lambda: {"installed_languages": ["elisp"]})

    core.instantiate_languages()

    assert requested == ["elisp"]


def test_get_all_language_names_includes_languages_without_scope_mapping():
    from src.utils import SCOPE_TO_LANGUAGE_NAME

    names = core.get_all_language_names()
    assert len(names) > 300  # tree_sitter_language_pack supports hundreds of languages
    assert set(SCOPE_TO_LANGUAGE_NAME.values()) < set(names)  # this plugin only maps a curated subset
    assert "elisp" in names  # a language with no Sublime scope mapping at all, still installable


def test_get_downloaded_language_names_reflects_disk_cache():
    # Populated by the session-scoped `_instantiate_languages` fixture, which calls `get_language` for `LANGUAGES`
    downloaded = core.get_downloaded_language_names()
    assert "python" in downloaded
    assert "javascript" in downloaded


def test_get_installed_language_names_excludes_removed_but_still_cached_language(monkeypatch):
    """
    Regression test: `remove_language` deliberately doesn't touch `tree_sitter_language_pack`'s on-disk cache (see its
    docstring) - so once a language's parser has been downloaded, it stays on disk even after the language is removed
    from `installed_languages`. The `TreeSitter: Remove Language` quick panel used to mark "installed" solely from the
    disk cache, so a removed language (e.g. `zsh`) kept showing up as ✅ forever. "Installed" now requires both.
    """
    import tree_sitter_language_pack as tslp

    monkeypatch.setattr(tslp, "downloaded_languages", lambda: ["python", "zsh"])  # "zsh" downloaded once, then removed
    monkeypatch.setattr(core, "get_settings_dict", lambda: {"installed_languages": ["python"]})

    installed = core.get_installed_language_names()

    assert installed == {"python"}
    assert "zsh" not in installed


def test_select_language_mixin_lists_every_tree_sitter_language_pack_language():
    """
    Regression test: the language picker used to only list languages this plugin has a Sublime scope mapping for
    (`SCOPE_TO_LANGUAGE_NAME`, a few dozen), even though `tree_sitter_language_pack` can install any of its hundreds of
    languages regardless of whether this plugin maps a scope to it.
    """

    class FakeWindow:
        def active_view(self):
            return None

        def show_quick_panel(self, items, on_select):
            self.items = items
            self.on_select = on_select

    mixin = core.TreeSitterSelectLanguageMixin()
    mixin.window = FakeWindow()  # type: ignore[assignment]
    mixin.run()

    assert len(mixin.languages) > 300
    assert "elisp" in mixin.languages  # no scope mapping, still listed as installable
    assert mixin.languages == sorted(mixin.languages)
    assert any("python" in item for item in mixin.window.items)


def test_scope_to_language_name_default_syntaxes_are_real_tree_sitter_language_pack_names():
    """
    `SCOPE_TO_LANGUAGE_NAME` maps several scopes for syntaxes Sublime Text ships out of the box (as opposed to
    community packages, whose scopes aren't worth chasing - see the comment above these entries in `src/utils.py`).
    Regression test: every language name on the right-hand side of that mapping must be one
    `tree_sitter_language_pack` actually recognizes, or `get_language` fails at parse time.
    """
    from tree_sitter_language_pack import manifest_languages

    from src.utils import SCOPE_TO_LANGUAGE_NAME

    manifest = set(manifest_languages())
    for scope, language in SCOPE_TO_LANGUAGE_NAME.items():
        assert language in manifest, f"{scope!r} maps to {language!r}, not a tree_sitter_language_pack language"


def test_check_scope_exact_match():
    assert core.check_scope("source.python") == "source.python"


def test_check_scope_prefix_match():
    # A more specific scope, e.g. from a `.sublime-syntax` that extends `source.yaml`, falls back to the base scope
    assert core.check_scope("source.python.something") == "source.python"


def test_check_scope_unsupported():
    assert core.check_scope("source.nonexistent") is None
    assert core.check_scope(None) is None


def test_parse_python():
    parser = Parser()
    tree = core.parse(parser, "source.python", "def f(x):\n    return x + 1\n")
    assert tree.root_node.type == "module"
    assert not tree.root_node.has_error


def test_parse_reuses_one_parser_across_languages():
    # `core.parse` sets `parser.language` on every call, so one `Parser` can parse different languages
    parser = Parser()
    py_tree = core.parse(parser, "source.python", "x = 1")
    js_tree = core.parse(parser, "source.js", "let x = 1;")
    assert py_tree.root_node.type == "module"
    assert js_tree.root_node.type == "program"


def test_byte_offset_ascii():
    s = "hello world"
    assert core.byte_offset(5, s) == 5


def test_byte_offset_multibyte():
    # "café" -> "caf" (3 ascii bytes) + "é" (2 bytes in utf-8); code-point offset 4 is after the "é"
    s = "café world"
    assert core.byte_offset(4, s) == 5


def _text_change(a_pt: int, b_pt: int, a_row: int, a_col: int, b_row: int, b_col: int, len_utf8: int, s: str):
    """
    Minimal stand-in for `sublime.TextChange`, which only exists inside Sublime. `get_edit`/`edit` only touch the
    attributes accessed below.
    """
    point = types.SimpleNamespace
    return types.SimpleNamespace(
        a=point(pt=a_pt, row=a_row, col_utf8=a_col),
        b=point(pt=b_pt, row=b_row, col_utf8=b_col),
        len_utf8=len_utf8,
        str=s,
    )


def test_edit_insertion_updates_tree_incrementally():
    parser = Parser()
    s = "def f():\n    pass\n"
    tree = core.parse(parser, "source.python", s)

    new_s = "def f(x):\n    pass\n"
    # Insert "x" at point 6 (right after "def f(")
    change = _text_change(a_pt=6, b_pt=6, a_row=0, a_col=6, b_row=0, b_col=6, len_utf8=0, s="x")

    new_tree = core.edit(parser, "source.python", [change], tree, s=s, new_s=new_s, debug=True)
    assert new_tree.root_node.type == "module"
    assert not new_tree.root_node.has_error
    params = new_tree.root_node.child(0).child_by_field_name("parameters")
    assert params is not None
    assert params.named_child_count == 1
    assert params.named_children[0].text == b"x"


def test_edit_deletion_updates_tree_incrementally():
    parser = Parser()
    s = "def f(x):\n    pass\n"
    tree = core.parse(parser, "source.python", s)

    new_s = "def f():\n    pass\n"
    # Delete "x" between points 6 and 7: `a` is the start of the deleted range, `b` is the end, s.t. a < b
    change = _text_change(a_pt=6, b_pt=7, a_row=0, a_col=6, b_row=0, b_col=7, len_utf8=1, s="")

    new_tree = core.edit(parser, "source.python", [change], tree, s=s, new_s=new_s, debug=True)
    params = new_tree.root_node.child(0).child_by_field_name("parameters")
    assert params is not None
    assert params.named_child_count == 0


class _FakeSyntax:
    scope = "source.python"


class _FakeView:
    """Minimal stand-in for `sublime.View`, mutated between `on_text_changed` calls like a real buffer would be."""

    def __init__(self, text: str):
        self.text = text

    def size(self):
        return len(self.text)

    def substr(self, region):
        return self.text[region.begin() : region.end()]

    def syntax(self):
        return _FakeSyntax()

    def file_name(self):
        return None

    def window(self):
        return None


class _FakeBuffer:
    def __init__(self, view: _FakeView, buffer_id: int):
        self.view = view
        self.buffer_id = buffer_id

    def primary_view(self):
        return self.view

    def id(self):
        return self.buffer_id


def test_text_change_listener_coalesces_stale_callbacks(monkeypatch):
    """
    Regression test for typing outpacing parsing: ST queues every text change's callback on one FIFO async thread, so
    if the parser can't keep up, several callbacks can pile up behind the most recent one. `on_text_changed` should
    collapse this - every scheduled callback except the freshest is a no-op, and the freshest one alone runs a single
    `edit()` that incorporates every change accumulated since the last one that actually ran.
    """
    import sublime

    scheduled_cbs = []
    monkeypatch.setattr(
        sublime, "set_timeout_async", lambda callback, delay=0: scheduled_cbs.append(callback), raising=False
    )

    buffer_id = -1  # distinct from any buffer id used by other tests
    s0 = "def f():\n    pass\n"
    s1 = "def f(x):\n    pass\n"
    s2 = "def f(xy):\n    pass\n"
    s3 = "def f(xyz):\n    pass\n"

    view = _FakeView(s0)
    listener = core.TreeSitterTextChangeListener()
    listener.buffer = _FakeBuffer(view, buffer_id)  # type: ignore[assignment]
    listener.debug = True  # force `edit`'s "changes replay to new_s" assertion to actually run

    parser = Parser()
    core.BUFFER_ID_TO_TREE[buffer_id] = core.make_tree_dict(
        core.parse(parser, "source.python", s0), s0, "source.python"
    )

    # Three keystrokes "fire" (main thread) before the async thread has run any of their callbacks - each inserts one
    # more character right after "def f(": "x", then "y", then "z"
    changes = [
        _text_change(a_pt=6, b_pt=6, a_row=0, a_col=6, b_row=0, b_col=6, len_utf8=0, s="x"),
        _text_change(a_pt=7, b_pt=7, a_row=0, a_col=7, b_row=0, b_col=7, len_utf8=0, s="y"),
        _text_change(a_pt=8, b_pt=8, a_row=0, a_col=8, b_row=0, b_col=8, len_utf8=0, s="z"),
    ]
    for change, text in zip(changes, [s1, s2, s3]):
        view.text = text
        listener.on_text_changed([change])

    assert len(scheduled_cbs) == 3
    assert listener.generation == 3

    # The two stale callbacks (for "x" and "y") must no-op: no change to the cached tree, nothing drained yet
    scheduled_cbs[0]()
    assert core.BUFFER_ID_TO_TREE[buffer_id]["s"] == s0
    scheduled_cbs[1]()
    assert core.BUFFER_ID_TO_TREE[buffer_id]["s"] == s0
    assert listener.pending_changes == changes

    # The freshest callback (for "z") does the real work, applying all three accumulated changes in one `edit()`
    scheduled_cbs[2]()
    tree_dict = core.BUFFER_ID_TO_TREE[buffer_id]
    assert tree_dict["s"] == s3
    assert not tree_dict["tree"].root_node.has_error
    assert listener.pending_changes == []

    params = tree_dict["tree"].root_node.child(0).child_by_field_name("parameters")
    assert params is not None
    assert params.named_children[0].text == b"xyz"


def test_text_change_listener_logs_slow_parse_even_without_debug(monkeypatch):
    """
    A `parse`/`edit` call slow enough to affect UI performance should be logged even when `debug` is off: `debug` logs
    every single text change, which is too noisy to leave on by default, but a slow parse is worth surfacing on its
    own so a user knows to reach for `language_name_to_debounce_ms` or `TreeSitter: Remove Language`.
    """
    import sublime

    monkeypatch.setattr(sublime, "set_timeout_async", lambda callback, delay=0: callback(), raising=False)
    monkeypatch.setattr(core, "SLOW_PARSE_MS", -1)  # any parse/edit "exceeds" this

    logged = []
    monkeypatch.setattr(core, "log", lambda *args, **kwargs: logged.append((args, kwargs)))

    buffer_id = -2  # distinct from any buffer id used by other tests
    s0 = "x = 1\n"
    view = _FakeView(s0)
    listener = core.TreeSitterTextChangeListener()
    listener.buffer = _FakeBuffer(view, buffer_id)  # type: ignore[assignment]
    assert listener.debug is False

    parser = Parser()
    core.BUFFER_ID_TO_TREE[buffer_id] = core.make_tree_dict(
        core.parse(parser, "source.python", s0), s0, "source.python"
    )

    view.text = "x = 12\n"
    change = _text_change(a_pt=5, b_pt=5, a_row=0, a_col=5, b_row=0, b_col=5, len_utf8=0, s="2")
    listener.on_text_changed([change])

    assert len(logged) == 1
    (message, *_rest), kwargs = logged[0]
    assert "took" in message
    assert kwargs.get("with_status") is True
