"""
Public-facing functions to interface with Tree-sitter, designed for use by other plugins.

Example usage: `from sublime_tree_sitter import get_tree_dict`
"""
from TreeSitter.src.api import (
    get_ancestor,
    get_ancestors,
    get_captures_from_nodes,
    get_cousins,
    get_descendant,
    get_larger_ancestor,
    get_node_spanning_region,
    get_region_from_node,
    get_scope_to_language_name,
    get_selected_nodes,
    get_sibling,
    get_tracked_buffer_ids,
    get_tree_dict,
    get_tree_from_code,
    get_view_from_buffer_id,
    goto_captures,
    query_node,
    query_node_with_s,
    show_node_under_selection,
    walk_tree,
)

__all__ = [
    "get_ancestor",
    "get_ancestors",
    "get_captures_from_nodes",
    "get_cousins",
    "get_descendant",
    "get_larger_ancestor",
    "get_node_spanning_region",
    "get_region_from_node",
    "get_scope_to_language_name",
    "get_selected_nodes",
    "get_sibling",
    "get_tracked_buffer_ids",
    "get_tree_dict",
    "get_tree_from_code",
    "get_view_from_buffer_id",
    "goto_captures",
    "query_node",
    "query_node_with_s",
    "show_node_under_selection",
    "walk_tree",
]
