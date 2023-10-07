"""
Public-facing functions to interface with Tree-sitter, designed for use by other plugins.

Example usage: `from sublime_tree_sitter import get_tree_dict`
"""
from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from TreeSitter.main import BUFFER_ID_TO_TREE, SCOPE_TO_LANGUAGE
from TreeSitter.src.utils import ScopeType

if TYPE_CHECKING:
    # So this module can be imported before `tree_sitter` installed
    from tree_sitter import Node, Parser, Tree

__all__ = ["get_tree_dicts", "get_tree_dict", "get_tree_from_code", "query_tree", "walk_tree"]


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
