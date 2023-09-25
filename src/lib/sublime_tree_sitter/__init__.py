"""
Public-facing functions for SublimeTreeSitter, designed for use by other plugins.

Usage: `from sublime_tree_sitter import get_tree`
"""
from __future__ import annotations

import copy

from SublimeTreeSitter.main import BUFFER_ID_TO_TREE, SCOPE_TO_LANGUAGE
from SublimeTreeSitter.src.utils import ScopeType
from tree_sitter import Node, Parser, Tree

__all__ = ["get_trees", "get_tree", "parse_tree", "query_tree", "walk_tree"]


def get_trees(buffer_id: int):
    return {buffer_id: copy.copy(tree) for buffer_id, tree in BUFFER_ID_TO_TREE.items()}


def get_tree(buffer_id: int):
    tree = BUFFER_ID_TO_TREE.get(buffer_id)
    return copy.copy(tree)


def parse_tree(scope: ScopeType, s: str | bytes):
    parser = Parser()
    if scope not in SCOPE_TO_LANGUAGE:
        return None
    parser.set_language(SCOPE_TO_LANGUAGE[scope])
    return parser.parse(s.encode() if isinstance(s, str) else s)


def query_tree(scope: ScopeType, query_s: str, tree_or_node: Tree | Node):
    if scope not in SCOPE_TO_LANGUAGE:
        return None
    language = SCOPE_TO_LANGUAGE[scope]
    query = language.query(query_s)
    return query.captures(tree_or_node.root_node if isinstance(tree_or_node, Tree) else tree_or_node)


def walk_tree(tree_or_node: Tree | Node):
    """
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
