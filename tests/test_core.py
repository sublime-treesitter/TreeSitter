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
    assert len(names) > 300  # tree_sitter_language_pack supports ~370 languages
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
    (`SCOPE_TO_LANGUAGE_NAME`, a few dozen), even though `tree_sitter_language_pack` can install any of its ~370
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
