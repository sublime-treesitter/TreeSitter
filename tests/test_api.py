from __future__ import annotations

import types
from pathlib import Path

from tree_sitter import Parser

from src import api, core

QUERIES_PATH = Path(__file__).parent.parent / "queries"

PYTHON_CODE = """
class Outer:
    def method_one(self):
        pass

    class Inner:
        def method_two(self):
            pass


def top_level():
    pass
"""


def _parse(scope: str, code: str):
    return core.parse(Parser(), scope, code).root_node


def _symbols_query(language_name: str) -> str:
    return (QUERIES_PATH / language_name / "symbols.scm").read_text()


def test_query_node_with_s_preserves_document_order():
    """
    `get_captures_from_nodes` breadcrumbs rely on ancestors being captured before their descendants (see the docstring
    on `query_node_with_s`). This is the main behavior we need from the `tree_sitter` 0.26 `Query`/`QueryCursor` APIs.
    """
    root = _parse("source.python", PYTHON_CODE)
    captures = api.query_node_with_s("source.python", root, _symbols_query("python"))
    assert captures is not None

    names_in_order = [(node.text, name) for node, name in captures]
    outer_idx = names_in_order.index((b"Outer", "definition.class"))
    method_one_idx = names_in_order.index((b"method_one", "definition.function"))
    inner_idx = names_in_order.index((b"Inner", "definition.class"))
    method_two_idx = names_in_order.index((b"method_two", "definition.function"))

    assert outer_idx < method_one_idx < inner_idx < method_two_idx


def test_get_captures_from_nodes_builds_breadcrumbs(monkeypatch):
    root = _parse("source.python", PYTHON_CODE)
    tree_dict = core.make_tree_dict(tree=types.SimpleNamespace(root_node=root), s=PYTHON_CODE, scope="source.python")
    monkeypatch.setattr(api, "get_tree_dict", lambda buffer_id: tree_dict)

    fake_view = types.SimpleNamespace(buffer_id=lambda: 1)
    captures = api.get_captures_from_nodes([root], fake_view, _symbols_query("python"))

    by_text = {c["node"].text: c for c in captures}
    assert [b["node"].text for b in by_text[b"Outer"]["breadcrumbs"]] == []
    assert [b["node"].text for b in by_text[b"method_one"]["breadcrumbs"]] == [b"Outer"]
    assert [b["node"].text for b in by_text[b"Inner"]["breadcrumbs"]] == [b"Outer"]
    assert [b["node"].text for b in by_text[b"method_two"]["breadcrumbs"]] == [b"Inner", b"Outer"]
    assert [b["node"].text for b in by_text[b"top_level"]["breadcrumbs"]] == []


def test_get_query_s_from_file_resolves_inherits_pragma():
    # `javascript/symbols.scm` starts with `; inherits: ecma`
    query_s = api.get_query_s_from_file("javascript", queries_path=QUERIES_PATH)
    assert "@definition.class" in query_s  # from javascript/symbols.scm
    assert "@definition.function" in query_s  # inherited from ecma/symbols.scm


def test_javascript_symbols_query_compiles_and_finds_class_fields():
    """
    Regression test: `ecma/symbols.scm` used to have a `public_field_definition` pattern (TypeScript/TSX's name for a
    class field), but the plain `javascript` grammar names the same node `field_definition` - so the fully resolved
    query (`javascript/symbols.scm` + inherited `ecma/symbols.scm`) failed to compile for a `.js` buffer
    (`tree_sitter.QueryError: Invalid node type ... public_field_definition`), crashing goto/select symbol even though
    nothing about the buffer itself was wrong. `javascript/symbols.scm` now defines its own pattern using the name its
    own grammar actually has, and `ecma/symbols.scm` no longer mentions either name.
    """
    code = "class Foo {\n  bar = () => 1\n}\n"
    root = _parse("source.js", code)
    query_s = api.get_query_s_from_file("javascript", queries_path=QUERIES_PATH)

    captures = api.query_node_with_s("source.js", root, query_s)
    assert captures is not None

    names = {(node.text, name) for node, name in captures}
    assert (b"Foo", "definition.class") in names
    assert (b"bar", "definition.function") in names


def test_typescript_symbols_query_still_finds_class_fields():
    # typescript/tsx's grammar names the same node `public_field_definition` - see the javascript test above
    code = "class Foo {\n  bar = () => 1\n}\n"
    root = _parse("source.ts", code)
    query_s = api.get_query_s_from_file("typescript", queries_path=QUERIES_PATH)

    captures = api.query_node_with_s("source.ts", root, query_s)
    assert captures is not None

    names = {(node.text, name) for node, name in captures}
    assert (b"Foo", "definition.class") in names
    assert (b"bar", "definition.function") in names


def test_get_query_s_from_file_missing_file_raises():
    try:
        api.get_query_s_from_file("nonexistent_language", queries_path=QUERIES_PATH)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError")


def test_get_query_s_from_file_ignores_missing_when_asked():
    query_s = api.get_query_s_from_file("nonexistent_language", queries_path=QUERIES_PATH, ignore_file_not_found=True)
    assert query_s == ""


def test_walk_tree_visits_every_node_once():
    # `tree_sitter` returns a fresh `Node` wrapper on each access, so compare by `.id` (stable per underlying node),
    # not Python object identity
    root = _parse("source.python", "x = 1\ny = 2\n")
    nodes = [n for n, _ in api.walk_tree(root)]
    assert nodes[0] == root
    assert len(nodes) == len({n.id for n in nodes})
    assert root.descendant_count == len(nodes)


def test_get_ancestors_includes_self_and_root():
    root = _parse("source.python", "x = 1\n")
    leaf = next(n for n, _ in api.walk_tree(root) if n.type == "integer")
    ancestors = api.get_ancestors(leaf)
    assert ancestors[0] == leaf
    assert ancestors[-1] == root


def test_contains():
    root = _parse("source.python", "x = 1\n")
    assignment = root.child(0)
    identifier = assignment.child_by_field_name("left")
    assert api.contains(root, assignment)
    assert api.contains(assignment, identifier)
    assert not api.contains(identifier, assignment)


def test_descendant_for_byte_range():
    root = _parse("source.python", "x = 1\n")
    identifier = api.descendant_for_byte_range(root, 0, 1)
    assert identifier is not None
    assert identifier.type == "identifier"
    assert identifier.text == b"x"


def test_get_field_name():
    root = _parse("source.python", "x = 1\n")
    assignment = root.child(0)
    left = assignment.child_by_field_name("left")
    right = assignment.child_by_field_name("right")
    assert api.get_field_name(left) == "left"
    assert api.get_field_name(right) == "right"


def test_get_tags_query_s_returns_bundled_query_when_available():
    query_s = api.get_tags_query_s("python")
    assert "@definition.function" in query_s
    assert "@definition.class" in query_s


def test_get_tags_query_s_empty_when_unavailable():
    # `markdown` doesn't ship a tags query in tree_sitter_language_pack
    assert api.get_tags_query_s("markdown") == ""
    assert api.get_tags_query_s("not-a-real-language") == ""


def test_get_captures_from_nodes_falls_back_to_tags_query(monkeypatch):
    """
    `get_tags_query_s`'s captures (`@definition.function` on the whole node) already match `CAPTURE_NAME_PREFIX`, so
    goto/select symbol works via the community "tags" convention with no other changes, just without breadcrumbs.
    """
    code = "def f():\n    pass\n"
    root = _parse("source.python", code)
    query_s = api.get_tags_query_s("python")

    tree_dict = core.make_tree_dict(tree=types.SimpleNamespace(root_node=root), s=code, scope="source.python")
    monkeypatch.setattr(api, "get_tree_dict", lambda buffer_id: tree_dict)

    fake_view = types.SimpleNamespace(buffer_id=lambda: 1)
    captures = api.get_captures_from_nodes([root], fake_view, query_s)

    assert len(captures) == 1
    assert captures[0]["name"] == "definition.function"
    # Unlike this plugin's own symbols.scm convention, tags.scm's @definition.<kind> captures the whole definition
    # node, not just its identifier - @name captures that separately (and is ignored here, since it isn't a
    # "definition.*" capture)
    assert captures[0]["node"].type == "function_definition"
    assert captures[0]["breadcrumb"] is None  # tags.scm has no @breadcrumb.N pragma
