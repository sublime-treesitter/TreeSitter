"""
Tests for `InjectedNode` and the injection-aware navigation built on it (`get_node_spanning_region` and everything
that uses it: select ancestor/sibling/descendant, show node under selection). `tree_sitter.Node.parent`/`.children`
know nothing about injections (see `core.compute_injections`) and never cross that boundary on their own;
`InjectedNode` is the "glue" that makes `.parent`/`.children` behave as if the outer tree and every tree injected
into it were one tree.
"""

from __future__ import annotations

import sublime

from src import api, core

# The exact repro from a real bug report: repeatedly expanding selection from inside a fenced JSON block used to get
# permanently stuck at the injection boundary, unable to reach the enclosing markdown structure.
JSON_FENCE_MARKDOWN = """```json
{
  "k": "v",
  "a": [1, 2, 3]
}
```

- Inline `code`
"""


class FakeView:
    """
    Just enough of `sublime.View` for the functions under test: a stable `buffer_id`, and `text_point_utf8` mapping
    (row, col) to a "point" the same way real Sublime does for ASCII text (this test content has no multibyte chars).
    """

    def __init__(self, buffer_id: int, s: str):
        self._buffer_id = buffer_id
        self._lines = s.splitlines(keepends=True)

    def buffer_id(self):
        return self._buffer_id

    def text_point_utf8(self, row: int, col: int) -> int:
        return sum(len(line) for line in self._lines[:row]) + col


def _make_tree_dict_and_view(monkeypatch, code: str, buffer_id: int = 1):
    from tree_sitter import Parser

    tree = core.parse(Parser(), "text.html.markdown", code)
    tree_dict = core.make_tree_dict(tree, code, "text.html.markdown")
    monkeypatch.setattr(api, "get_tree_dict", lambda bid: tree_dict if bid == buffer_id else None)
    return tree_dict, FakeView(buffer_id, code)


def _expand_ancestor_types(view: FakeView, region: sublime.Region, max_steps: int = 20) -> list[str]:
    """
    Simulate repeatedly running `TreeSitter: Select Ancestor` from `region`, the way a user actually triggers the bug:
    each step re-resolves `get_node_spanning_region` from the *previous* step's resulting region, exactly like the
    real command does, rather than reusing an already-resolved node.
    """
    types = []
    for _ in range(max_steps):
        ancestor = api.get_ancestor(region, view)
        if ancestor is None:
            break
        types.append(ancestor.type)
        region = api.get_region_from_node(ancestor, view)
    return types


def test_select_ancestor_escapes_fenced_json_block(monkeypatch):
    _, view = _make_tree_dict_and_view(monkeypatch, JSON_FENCE_MARKDOWN)

    start = JSON_FENCE_MARKDOWN.index("1")  # inside the JSON array, well below the injection boundary
    types = _expand_ancestor_types(view, sublime.Region(start, start + 1))

    # Escapes into the outer markdown tree, and - this was the actual bug - never falls back into the JSON one
    assert "fenced_code_block" in types
    escape_idx = types.index("fenced_code_block")
    assert not any(t in {"object", "pair", "array", "number", "string"} for t in types[escape_idx:])


def test_select_ancestor_escapes_inline_code_span(monkeypatch):
    _, view = _make_tree_dict_and_view(monkeypatch, JSON_FENCE_MARKDOWN)

    start = JSON_FENCE_MARKDOWN.index("code")  # inside the inline code span, inside a list item
    types = _expand_ancestor_types(view, sublime.Region(start, start + 1))

    assert "code_span" in types  # from the injected markdown_inline tree
    assert "list_item" in types  # from the outer markdown tree, several levels above the injection
    escape_idx = types.index("list_item")
    assert not any(t in {"code_span", "inline"} for t in types[escape_idx:])


def test_get_node_spanning_region_descends_into_injection(monkeypatch):
    _, view = _make_tree_dict_and_view(monkeypatch, JSON_FENCE_MARKDOWN)

    start = JSON_FENCE_MARKDOWN.index("1")
    node = api.get_node_spanning_region((start, start + 1), view.buffer_id())

    assert node is not None
    assert node.type == "number"  # from the *injected* JSON tree, not a flat markdown token
    assert node.raw.parent is not None  # real parent within the JSON tree; no crossing needed yet


def test_get_ancestors_crosses_injection_boundary(monkeypatch):
    _, view = _make_tree_dict_and_view(monkeypatch, JSON_FENCE_MARKDOWN)

    start = JSON_FENCE_MARKDOWN.index("1")
    node = api.get_node_spanning_region((start, start + 1), view.buffer_id())
    assert node is not None

    ancestor_types = [a.type for a in api.get_ancestors(node)]

    # JSON's own root node happens to also be named "document" (collides with markdown's), so anchor on
    # `code_fence_content` (unambiguously markdown's) and check it's followed by real markdown ancestors
    content_idx = ancestor_types.index("code_fence_content")
    fence_idx = ancestor_types.index("fenced_code_block")
    assert content_idx < fence_idx
    assert ancestor_types[-1] == "document"  # the outermost ancestor: markdown's true document root
    assert ancestor_types.count("document") == 2  # JSON's root, and markdown's - two different nodes, same name


def _code_fence_content_range(view: FakeView, buffer_id: int) -> tuple[int, int]:
    """
    The exact byte range of `code_fence_content` (i.e. the injected JSON tree's root): found structurally, rather
    than guessed from string offsets, so it's exactly right regardless of incidental whitespace/formatting.
    """
    tree_dict = api.get_tree_dict(buffer_id)
    assert tree_dict is not None

    def find(node):
        if node.type == "code_fence_content":
            return node
        for child in node.children:
            if found := find(child):
                return found
        return None

    node = find(tree_dict["tree"].root_node)
    assert node is not None
    return node.start_byte, node.end_byte


def test_get_sibling_crosses_injection_boundary(monkeypatch):
    _, view = _make_tree_dict_and_view(monkeypatch, JSON_FENCE_MARKDOWN)

    # Select the *whole* injected JSON content, so `get_node_spanning_region` resolves to the injected tree's root
    # itself (no native `.parent`), forcing the sibling search to cross via `InjectedNode`
    start, end = _code_fence_content_range(view, view.buffer_id())
    node = api.get_node_spanning_region((start, end), view.buffer_id())
    assert node is not None and node.raw.parent is None  # the injected tree's root

    sibling = api.get_sibling(sublime.Region(start, end), view, forward=True)

    assert sibling is not None
    assert sibling.type == "fenced_code_block_delimiter"  # the outer `code_fence_content`'s real next sibling


def test_get_descendant_enters_injection(monkeypatch):
    _, view = _make_tree_dict_and_view(monkeypatch, JSON_FENCE_MARKDOWN)

    start, end = _code_fence_content_range(view, view.buffer_id())
    descendant = api.get_descendant(sublime.Region(start, end), view)

    assert descendant is not None
    assert descendant.raw.parent is not None  # a real node inside the injected JSON tree, not the injected root


def test_get_cousins_does_not_cross_injection_boundary(monkeypatch):
    """
    Unlike ancestor/sibling/descendant, `get_cousins` deliberately doesn't cross injection boundaries: "same depth,
    same type" only means something within one grammar.
    """
    _, view = _make_tree_dict_and_view(monkeypatch, JSON_FENCE_MARKDOWN)

    start = JSON_FENCE_MARKDOWN.index("1")
    cousins = api.get_cousins(sublime.Region(start, start + 1), view, which="all")

    # "1", "2", "3" are cousins within the JSON tree; nothing from the outer markdown tree should appear
    assert all(c.type == "number" for c in cousins)
    assert len(cousins) >= 2


def test_injected_node_proxies_attributes_and_compares_by_wrapped_node(monkeypatch):
    _, view = _make_tree_dict_and_view(monkeypatch, JSON_FENCE_MARKDOWN)

    start = JSON_FENCE_MARKDOWN.index("1")
    node = api.get_node_spanning_region((start, start + 1), view.buffer_id())
    assert node is not None

    assert node.type == "number"
    assert node.start_byte == start
    assert node == api.InjectedNode(node.raw, [])  # equality is by wrapped node, regardless of injection context
