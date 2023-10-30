"""
Public-facing functions to interface with Tree-sitter, designed for use by other plugins.

Example usage: `from sublime_tree_sitter import get_tree_dict`
"""
from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Tuple

import sublime
from TreeSitter.main import BUFFER_ID_TO_TREE, SCOPE_TO_LANGUAGE
from TreeSitter.src.utils import ScopeType, byte_offset, maybe_none

if TYPE_CHECKING:
    # So this module can be imported before `tree_sitter` installed
    from tree_sitter import Node, Parser, Tree

__all__ = [
    "get_view_from_buffer_id",
    "get_tree_dicts",
    "get_tree_dict",
    "has_tree",
    "get_tree_from_code",
    "query_tree",
    "walk_tree",
    "get_node_spanning_region",
    "get_region_from_node",
    "get_larger_ancestor",
    "get_ancestor",
    "get_size",
]


def get_view_from_buffer_id(buffer_id: int) -> sublime.View | None:
    """
    Utilify function. Ensures `None` returned if a "dead" buffer id passed.
    """
    buffer = sublime.Buffer(buffer_id)
    view = buffer.primary_view()
    return view if maybe_none(view.id()) is not None else None


def get_tree_dicts():
    """
    Get all tree dicts being maintained for buffers.
    """
    return {buffer_id: copy.copy(tree) for buffer_id, tree in BUFFER_ID_TO_TREE.items()}


def get_tree_dict(buffer_id: int):
    """
    Get tree dict being maintained for this buffer.
    """
    tree = BUFFER_ID_TO_TREE.get(buffer_id)
    return copy.copy(tree) if tree else None


def has_tree(buffer_id: int):
    """
    Are we maintaining a tree for this buffer?
    """
    return buffer_id in BUFFER_ID_TO_TREE


def get_tree_from_code(scope: ScopeType, s: str | bytes):
    """
    Get a syntax tree back for source code `s`.
    """
    parser = Parser()
    if scope not in SCOPE_TO_LANGUAGE:
        return None
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


def get_node_spanning_region(region: sublime.Region | Tuple[int, int], buffer_id: int) -> Node | None:
    """
    Get smallest node spanning region, s.t. node's start point is less than or equal to region's start point, and
    node's end point is greater than or equal region's end point.

    If there are two nodes matching a zero-width region, prefer the "deeper" of the two, i.e. the one furthest from the
    root of the tree.
    """
    tree_dict = BUFFER_ID_TO_TREE.get(buffer_id)
    if not tree_dict:
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
