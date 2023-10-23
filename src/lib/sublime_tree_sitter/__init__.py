"""
Public-facing functions to interface with Tree-sitter, designed for use by other plugins.

Example usage: `from sublime_tree_sitter import get_tree_dict`
"""
from __future__ import annotations

import copy
from typing import TYPE_CHECKING, List, Tuple

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
    "get_tree_from_code",
    "query_tree",
    "walk_tree",
    "get_node_at_point",
    "get_node_spanning_region",
    "get_region_from_node",
    "get_larger_ancestor",
    "get_larger_region",
]


def get_view_from_buffer_id(buffer_id: int) -> sublime.View | None:
    buffer = sublime.Buffer(buffer_id)
    view = buffer.primary_view()
    return view if maybe_none(view.id()) is not None else None


def get_tree_dicts():
    return {buffer_id: copy.copy(tree) for buffer_id, tree in BUFFER_ID_TO_TREE.items()}


def get_tree_dict(buffer_id: int):
    tree = BUFFER_ID_TO_TREE.get(buffer_id)
    return copy.copy(tree) if tree else None


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

    reached_root = False
    while not reached_root:
        yield cursor.node

        if cursor.goto_first_child():
            continue

        if cursor.goto_next_sibling():
            continue

        retracing = True
        while retracing:
            if not cursor.goto_parent():
                retracing = False
                reached_root = True

            if cursor.goto_next_sibling():
                retracing = False


def get_node_at_point(point: int, buffer_id: int) -> Node | None:
    """
    Args:

    - `point`: Offset in UTF-8 code points from beginning of buffer
    - `buffer_id`: Buffer id for buffer that contains point

    We try to return the "smallest" node that contains point, where "contains" means the node's start point is less
    than or equal to point, and the node's end point is greater than or equal to point.

    If the only node containing point is the tree's root node, or if we have no tree for buffer id, we return `None`.

    ## References

    - [Emacs](https://www.gnu.org/software/emacs/manual/html_node/elisp/Retrieving-Nodes.html)
    - [Neovim](https://github.com/nvim-treesitter/nvim-treesitter/blob/master/lua/nvim-treesitter/ts_utils.lua)
    """

    tree_dict = BUFFER_ID_TO_TREE.get(buffer_id)
    if not tree_dict:
        return None

    s = tree_dict["s"]
    return get_node_at_point_from_tree(byte_offset(point, s), tree_dict["tree"].root_node)


def contains(node: Node, p: int):
    """
    Does `node` contain byte position `p`?
    """
    return node.start_byte <= p and node.end_byte >= p


def get_node_at_point_from_tree(p: int, tree_or_node: Tree | Node) -> Node | None:
    """
    Helper function for `get_node_at_point`.

    Args:

    - `p`: Offset in bytes from beginning of buffer
    - `tree_or_node`: Under which to look for most granular node containing point
    """
    from tree_sitter import Tree

    node = tree_or_node.root_node if isinstance(tree_or_node, Tree) else tree_or_node
    children = node.children

    if contains(node, p):
        if not children:
            # This is a leaf node, and it contains p, so we're done
            return node
        # Node contains p, but we can recurse into children to get more specific
        return get_node_at_point_from_tree(p, children[0])

    next_sibling = node.next_sibling
    siblings_cannot_contain_point = node.start_byte > p or (node.end_byte < p and not next_sibling)

    if siblings_cannot_contain_point:
        parent = node.parent
        if parent and parent.parent and contains(parent, p):
            return parent

    else:
        if next_sibling:
            return get_node_at_point_from_tree(p, next_sibling)

    return None


def get_ancestors(node: Node) -> List[Node]:
    """
    Get all ancestors of node, including node itself.
    """
    nodes: List[Node] = []
    current_node: Node | None = node

    while current_node:
        nodes.append(current_node)
        current_node = current_node.parent
    return nodes


def get_node_spanning_region(region: sublime.Region | Tuple[int, int], buffer_id: int) -> Node | None:
    """
    Like `get_node_at_point`, but gets "smallest" node spanning region, s.t. node's start point is less than or equal to
    region's start point, and node's end point is greater than or equal region's end point.
    """
    tree_dict = BUFFER_ID_TO_TREE.get(buffer_id)
    if not tree_dict:
        return None

    region = region if isinstance(region, sublime.Region) else sublime.Region(*region)
    root_node = tree_dict["tree"].root_node
    s = tree_dict["s"]

    start_node = get_node_at_point_from_tree(byte_offset(region.begin(), s), root_node)
    end_node = get_node_at_point_from_tree(byte_offset(region.end(), s), root_node)

    if not start_node or not end_node:
        return None

    start_nodes = get_ancestors(start_node)
    end_nodes = get_ancestors(end_node)

    for sn in start_nodes:
        for en in end_nodes:
            if sn.id == en.id:
                return sn

    return None


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


def get_larger_region(region: sublime.Region, view: sublime.View, reverse: bool = True) -> sublime.Region | None:
    """
    Useful for e.g. expanding selection. If larger region than `region` can be found, returns larger region, and node to
    which it corresponds.

    Does not return region corresponding to root node, i.e. region that spans entire buffer, because there are easier
    ways to do this…
    """
    node = get_node_spanning_region(region, view.buffer_id())

    if not node or not node.parent:
        return None

    new_region = get_region_from_node(node, view, reverse=reverse)
    if len(new_region) > len(region):
        return new_region

    ancestor = get_larger_ancestor(node) or node.parent
    if not ancestor.parent:
        return None
    return get_region_from_node(ancestor, view, reverse=reverse)
