from __future__ import annotations

import types

from tree_sitter import Parser

from src import core


def test_instantiate_languages_populates_scope_to_language():
    # Populated by the session-scoped `_instantiate_languages` fixture
    assert "source.python" in core.SCOPE_TO_LANGUAGE
    assert "source.js" in core.SCOPE_TO_LANGUAGE


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
