from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, List, Literal, Tuple, cast

import sublime
import sublime_plugin

from .core import (
    BUFFER_ID_TO_TREE,
    SCOPE_TO_LANGUAGE,
    byte_offset,
    check_scope,
    get_scope,
    get_view_text,
    make_tree_dict,
    parse,
    publish_tree_update,
    trim_cached_trees,
)
from .utils import QUERIES_PATH, get_scope_to_language_name, maybe_none, not_none

if TYPE_CHECKING:
    from tree_sitter import Node, Tree

    CaptureType = Tuple[Node, str, List[Node], int]

SYMBOLS_FILE = "symbols.scm"

#
# Public-facing API functions, and some helper functions
#


def get_tracked_buffer_ids():
    """
    Get buffer ids for all tracked buffers.
    """
    return list(BUFFER_ID_TO_TREE.keys())


def get_tree_dict(buffer_id: int):
    """
    Get tree dict being maintained for this buffer, or instantiate new tree dict on the fly.
    """
    if not isinstance(cast(Any, buffer_id), int) or not (view := get_view_from_buffer_id(buffer_id)):
        return
    if not (scope := get_scope(view)) or not (scope := check_scope(scope)):
        BUFFER_ID_TO_TREE.pop(buffer_id, None)
        return

    tree_dict = BUFFER_ID_TO_TREE.get(buffer_id)
    if not tree_dict or tree_dict["scope"] != scope:
        from tree_sitter import Parser

        view_text = get_view_text(view)
        BUFFER_ID_TO_TREE[buffer_id] = make_tree_dict(parse(Parser(), scope, view_text), view_text, scope)
        trim_cached_trees()
        publish_tree_update(view.window(), buffer_id=buffer_id, scope=scope)

    return BUFFER_ID_TO_TREE.get(buffer_id)


def get_view_from_buffer_id(buffer_id: int) -> sublime.View | None:
    """
    Utilify function. Ensures `None` returned if a "dead" buffer id passed.
    """
    buffer = sublime.Buffer(buffer_id)
    view = buffer.primary_view()
    return view if maybe_none(view.id()) is not None else None


def get_tree_from_code(scope: str, s: str | bytes):
    """
    Get a syntax tree back for source code `s`.
    """
    from tree_sitter import Parser

    if not (validated_scope := check_scope(scope)):
        return None
    parser = Parser()
    parser.set_language(SCOPE_TO_LANGUAGE[validated_scope])
    return parser.parse(s.encode() if isinstance(s, str) else s)


def query_node_with_s(scope: str | None, query_s: str, node: Node):
    """
    Query a node with `query_s`.

    See https://github.com/tree-sitter/py-tree-sitter#pattern-matching
    """
    if not (scope := check_scope(scope)):
        return
    language = SCOPE_TO_LANGUAGE[scope]
    query = language.query(query_s)
    return query.captures(node)


def get_query_s_from_file(queries_path: str | Path, query_file: str, language_name: str) -> str:
    """
    Handle `inherits` "pragmas" of the following structure: `; inherits: lang(,other_lang)`
    """
    INHERITS_PREFIX = "; inherits:"
    path = Path(queries_path) / language_name / query_file

    with open(path, "r") as f:
        query_s = f.read()

    languages: list[str] = []
    with open(path, "r") as f:
        for line in f:
            if line.startswith(INHERITS_PREFIX):
                languages = [lang.strip() for lang in line.split(INHERITS_PREFIX)[1].split(",") if lang]

    queries = [get_query_s_from_file(queries_path, query_file=query_file, language_name=lang) for lang in languages]
    return "\n".join([query_s, *queries])


def query_node(
    scope: str | None,
    node: Node,
    query_file: str,
    queries_path: str | Path = "",
):
    """
    Query a node with a prepared query.
    """
    if not (scope := check_scope(scope)):
        return
    language_name = get_scope_to_language_name()[scope]

    queries_path = os.path.expanduser(queries_path or QUERIES_PATH)
    query_s = get_query_s_from_file(queries_path, query_file=query_file, language_name=language_name)
    return query_node_with_s(scope, query_s, node)


def walk_tree(tree_or_node: Tree | Node, max_depth: int | None = None):
    """
    Walk all the nodes under `tree_or_node`.

    See https://github.com/tree-sitter/py-tree-sitter/issues/33#issuecomment-864557166
    """
    cursor = tree_or_node.walk()
    depth = 0

    reached_root = False
    while not reached_root:
        yield cursor.node, depth

        if max_depth is None or depth < max_depth:
            # Don't walk children if we've already reached `max_depth`
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
    return node.descendant_for_byte_range(start_byte, end_byte)


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


def get_depth(node: Node) -> int:
    """
    Get 0-based depth of node relative to tree's `root_node`.
    """
    return len(get_ancestors(node)) - 1


def get_node_spanning_region(region: sublime.Region | tuple[int, int], buffer_id: int) -> Node | None:
    """
    Get smallest node spanning region, s.t. node's start point is less than or equal to region's start point, and
    node's end point is greater than or equal region's end point.

    If there are two nodes matching a zero-width region, prefer the "deeper" of the two, i.e. the one furthest from the
    root of the tree.
    """
    if not (tree_dict := get_tree_dict(buffer_id)):
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


def get_view_name(view: sublime.View):
    if name := view.file_name():
        return os.path.basename(name)

    return view.name() or ""


def scroll_to_region(region: sublime.Region, view: sublime.View):
    view.show(region.b)


def get_descendant(region: sublime.Region, view: sublime.View) -> Node | None:
    """
    Find node that spans region, then find first descendant that's smaller than this node. This descendant is basically
    guaranteed to have at least one sibling.
    """
    if not (tree_dict := get_tree_dict(view.buffer_id())):
        return

    node = get_node_spanning_region(region, view.buffer_id()) or tree_dict["tree"].root_node
    for desc, _ in walk_tree(node):
        if get_size(desc) < get_size(node):
            return desc


def get_sibling(region: sublime.Region, view: sublime.View, forward: bool = True) -> Node | None:
    """
    - Find node that spans region
    - Find "first" ancestor of this node, including node itself, that has siblings
        - If node spanning region is root node, find "first" descendant that has siblings
    - Return the next or previous sibling
    """
    node = get_node_spanning_region(region, view.buffer_id())
    if not node:
        return

    if not node.parent:
        # We're at root node, so we find the first descendant that has siblings, and return sibling adjacent to region
        tree_dict = get_tree_dict(view.buffer_id())
        first_sibling = get_descendant(region, view)

        if first_sibling and first_sibling.parent and tree_dict:
            begin = byte_offset(region.begin(), tree_dict["s"])
            if forward:
                for sibling in first_sibling.parent.children:
                    if begin <= sibling.start_byte:
                        return sibling
            else:
                for sibling in reversed(first_sibling.parent.children):
                    if begin >= sibling.start_byte:
                        return sibling

        return first_sibling

    while node.parent and node.parent.parent:
        if len(node.parent.children) == 1:
            node = node.parent
        else:
            break

    siblings = not_none(node.parent).children
    idx = siblings.index(node)
    idx = idx + 1 if forward else idx - 1
    return siblings[idx % len(siblings)]


WhichCousinsType = Literal["next", "previous", "all"]


def get_cousins(
    region: sublime.Region,
    view: sublime.View,
    same_types: bool = True,
    same_text: bool = False,
    which: WhichCousinsType = "all",
) -> list[Node]:
    """
    Find node that spans region, and return next/previous/all nodes that:

    - Are at same depth in tree
    - If `same_types` is `True`, have same `type`, and have ancestors of the same `type`s
    - If `same_text` is `True`, have same `text`
    """
    node = get_node_spanning_region(region, view.buffer_id())
    if not node or not node.parent:
        return []

    ancestors = get_ancestors(node)
    ancestor_types = [ancestor.type for ancestor in ancestors]
    node_depth = len(ancestors) - 1

    cousins: list[Node] = []
    for cousin, depth in walk_tree(ancestors[-1], max_depth=node_depth):
        if depth != node_depth:
            continue
        if same_text and cousin.text != node.text:
            continue
        if same_types and [ancestor.type for ancestor in get_ancestors(cousin)] != ancestor_types:
            continue
        cousins.append(cousin)

    if which == "all":
        return cousins

    if which == "next":
        for cousin in cousins:
            if cousin.start_byte > node.start_byte:
                return [cousin]
        return [cousins[0]]

    for cousin in reversed(cousins):
        if cousin.start_byte < node.start_byte:
            return [cousin]
    return [cousins[-1]]


def get_selected_nodes(view: sublime.View) -> list[Node]:
    """
    Get nodes selected in `view`.
    """
    nodes: list[Node] = []
    for region in view.sel():
        if len(region) > 0:
            node = get_node_spanning_region(region, view.buffer_id())
            if node:
                nodes.append(node)

    return nodes


def render_debug_view(view: sublime.View, name: str, text: str):
    """
    Note that as of Sublime build 4166, we can't use `View.insert`, because the `edit` token for the "previous" view
    isn't valid for the newly created view.

    [See more here](https://github.com/sublimehq/sublime_text/issues/6177#issuecomment-1781019459).
    """
    if not (window := view.window()):
        return

    new_view = window.new_file()
    new_view.set_name(name)
    new_view.set_scratch(True)
    new_view.run_command("append", {"characters": text})


def render_node_html(pairs: Iterable[tuple[str, str]]):
    """
    For use in `show_node_under_selection`.
    """
    sp = "&nbsp;"
    max_key_len = max(len(k) for (k, _) in pairs)

    info_list = "<br/>".join(f"<b>{k}{sp * (max_key_len - len(k))}</b>{sp}{sp}{v}" for (k, v) in pairs)
    copy_button = '<a href="">copy</a>'

    return f'<body id="tree-sitter-node-info">{info_list}<br/><br/>{copy_button}</body>'


def show_node_under_selection(view: sublime.View, select: bool, **kwargs):
    """
    Render a popup with info about the node under the first cursor/selection. If there are multiple nodes with the same
    size spanning this selection, show info for them all.

    Inspired by https://github.com/nvim-treesitter/playground.
    """
    if not (sel := view.sel()):
        return

    if not (node := get_node_spanning_region(sel[0], view.buffer_id())) or not node.parent:
        return

    if select:
        sel.add(get_region_from_node(node, view, reverse=True))

    tree_dict = not_none(get_tree_dict(view.buffer_id()))

    nodes = [node]
    while node.parent and get_size(node) == get_size(node.parent):
        node = node.parent
        nodes.append(node)

    pairs: list[tuple[str, str]] = [
        ("type", nodes[0].type),
        ("depth", str(get_depth(nodes[0]))),
        ("range", f"{node.start_point} → {node.end_point}"),
        ("lang", get_scope_to_language_name()[tree_dict["scope"]]),
        ("scope", tree_dict["scope"]),
    ]
    for node in nodes[1:]:
        pairs.insert(0, ("", "➔"))
        pairs.insert(0, ("depth", str(get_depth(node))))
        pairs.insert(0, ("type", node.type))

    def on_navigate(href: str):
        sublime.set_clipboard("\n".join(pair[1] for pair in pairs))
        sublime.status_message("node info copied")

    view.show_popup(
        render_node_html(pairs),
        on_navigate=on_navigate,
        **kwargs,
    )


CaptureNameType = Literal[
    "definition.type",
    "definition.class",
    "definition.var",
    "definition.function",
    "definition.call",
    "definition.object",
    "definition.interface",
    "definition.element",
]

CAPTURE_NAME_TO_KIND: dict[CaptureNameType, sublime.Kind] = {
    "definition.class": (sublime.KindId.TYPE, "c", "c"),
    "definition.type": (sublime.KindId.TYPE, "t", "t"),
    "definition.interface": (sublime.KindId.TYPE, "i", "i"),
    "definition.var": (sublime.KindId.VARIABLE, "v", "v"),
    "definition.function": (sublime.KindId.FUNCTION, "f", "f"),
    "definition.call": (sublime.KindId.COLOR_ORANGISH, "l", "l"),
    "definition.object": (sublime.KindId.VARIABLE, "o", "o"),
    "definition.element": (sublime.KindId.VARIABLE, "e", "e"),
}


def parse_capture_name(capture_name: str) -> Tuple[str, int | None]:
    """
    Parse capture name and optionally captured node depth, compared with "container" depth, for rendering breadcrumbs.
    """
    parts = capture_name.split(".depth.", 1)
    return (parts[0], int(parts[1])) if len(parts) == 2 else (parts[0], None)


BLOCK_CAPTURE_NAME = "definition.block"


def get_captures_from_nodes(
    nodes: list[Node],
    view: sublime.View,
    query_file: str = SYMBOLS_FILE,
    queries_path: str | Path = "",
) -> List[CaptureType]:
    """
    Get capture tuples from search nodes. Capture tuples include captured ancestors for rendering breadcrumbs.

    Raises:
        `FileNotFoundError` if query file doesn't exist
    """

    if not (tree_dict := get_tree_dict(view.buffer_id())):
        return []

    container_id_to_captured_node: dict[int, Tuple[Node, str]] = {}
    captures: list[CaptureType] = []

    for search_node in nodes:
        for captured_node, capture_name in query_node(tree_dict["scope"], search_node, query_file, queries_path) or []:
            _, depth = parse_capture_name(capture_name)

            container = captured_node
            if depth is not None or capture_name == BLOCK_CAPTURE_NAME:
                for _ in range(depth or 0):
                    container = not_none(container.parent)
                container_id_to_captured_node[container.id] = (captured_node, capture_name)

            # Exclude search_node from ancestors, user already knows they're searching this node
            captured_ancestors = [
                container_id_to_captured_node[a.id]
                for a in get_ancestors(container)[1:]
                if a.id in container_id_to_captured_node and a.id != search_node.id
            ]
            if capture_name != BLOCK_CAPTURE_NAME:
                captures.append(
                    (
                        captured_node,
                        capture_name,
                        [node for node, name in captured_ancestors if name != BLOCK_CAPTURE_NAME],
                        len(captured_ancestors),
                    )
                )

    return captures


def get_capture_kind(name: str) -> sublime.Kind:
    """
    For rendering `QuickPanelItem`s.
    """
    name, _ = parse_capture_name(name)
    if name not in CAPTURE_NAME_TO_KIND:
        return (sublime.KindId.AMBIGUOUS, "?", "?")

    return CAPTURE_NAME_TO_KIND[name]


def on_highlight_repaint_view(view: sublime.View):
    """
    Works around ST quick panel rendering bug. Modifying selection in `on_highlight` callback has no effect unless
    viewport moves or its contents change.
    """
    DY = 2

    x, y = view.viewport_position()
    if y == 0:
        view.set_viewport_position((x, DY))
        view.set_viewport_position((x, 0))
    else:
        view.set_viewport_position((x, y - DY))
        view.set_viewport_position((x, y))


def goto_captures(captures: list[CaptureType], view: sublime.View):
    """
    Render goto options in quick panel in `view`, from list of `captures`. Captures can be gotten with
    `get_captures_from_nodes`.
    """

    def format_node_text(text: str):
        if " " not in text:
            return text
        return " ".join(text.split())

    def format_breadcrumbs(ancestors: list[Node]):
        return " ➔ ".join(format_node_text(a.text.decode()) for a in reversed(ancestors))

    indent = " " * 4
    options: list[sublime.QuickPanelItem] = []
    for node, capture_name, ancestors, depth in captures:
        options.append(
            sublime.QuickPanelItem(
                trigger=f"{indent * depth}{format_node_text(node.text.decode())}",
                kind=get_capture_kind(capture_name),
                annotation=format_breadcrumbs(ancestors),
            )
        )

    def on_highlight(idx: int):
        """
        Scroll to symbol and select it.
        """
        node, _, _, _ = captures[idx]
        a = view.text_point_utf8(*node.start_point)
        b = view.text_point_utf8(*node.end_point)
        region = sublime.Region(a, b)

        sel = view.sel()
        sel.clear()
        scroll_to_region(region, view)
        on_highlight_repaint_view(view)  # Works around ST quick panel `on_highlight` rendering bug
        sel.add(region)

    regions = [r for r in view.sel()]
    xy = view.viewport_position()

    # Find capture nearest to first selected region, and open quick panel at this index
    selected_index = -1
    if regions:
        row, _ = view.rowcol(regions[0].begin())
        for idx, (node, _, _, _) in enumerate(captures):
            if row >= node.start_point[0]:
                selected_index = idx

    def on_select(idx: int):
        """
        If user "cancels" selection, revert selection and viewport position to initial values.
        """
        if idx == -1:
            view.set_viewport_position(xy)
            sel = view.sel()
            sel.clear()
            sel.add_all(regions)

    window = not_none(view.window())
    window.show_quick_panel(options, on_select=on_select, on_highlight=on_highlight, selected_index=selected_index)


#
# Select, query and debug commands
#


class TreeSitterSelectAncestorCommand(sublime_plugin.TextCommand):
    """
    Expand selection to smallest ancestor that's bigger than node spanning currently selected region.

    Does not select region corresponding to root node, i.e. region that spans entire buffer, because there are easier
    ways to do this…
    """

    def run(self, edit, reverse_sel: bool = True):
        sel = self.view.sel()
        new_region: sublime.Region | None = None

        for region in sel:
            new_node = get_ancestor(region, self.view)
            if new_node and new_node.parent:
                new_region = get_region_from_node(new_node, self.view, reverse=reverse_sel)
                self.view.sel().add(new_region)

        if new_region and len(sel) == 1:
            scroll_to_region(new_region, self.view)


class TreeSitterSelectSiblingCommand(sublime_plugin.TextCommand):
    """
    Find node spanning selected region, then find its next or previous sibling (depending on value of `forward`), and
    select this region or extend current selection (depending on value of `extend`).
    """

    def run(self, edit, forward: bool = True, extend: bool = False, reverse_sel: bool = True):
        sel = self.view.sel()
        new_regions: list[sublime.Region] = []

        for region in sel:
            if sibling := get_sibling(region, self.view, forward):
                new_region = get_region_from_node(sibling, self.view, reverse=reverse_sel)
                new_regions.append(new_region)
                if not extend:
                    sel.subtract(region)
                sel.add(new_region)

        if new_regions:
            scroll_to_region(new_regions[-1] if forward else new_regions[0], self.view)


class TreeSitterSelectCousinsCommand(sublime_plugin.TextCommand):
    """
    Find node spanning selected region, then find its next or previous cousin, or all cousins (depending on value of
    `which`), and select these regions or extend current selection (depending on value of `extend`).
    """

    def run(
        self,
        edit,
        same_types: bool = True,
        same_text: bool = False,
        which: WhichCousinsType = "all",
        extend: bool = False,
        reverse_sel: bool = True,
    ):
        sel = self.view.sel()
        new_regions: list[sublime.Region] = []

        for region in sel:
            for cousin in get_cousins(region, self.view, same_types=same_types, same_text=same_text, which=which):
                new_region = get_region_from_node(cousin, self.view, reverse=reverse_sel)
                new_regions.append(new_region)
                if which != "all" and not extend:
                    sel.subtract(region)
                sel.add(new_region)

        if new_regions and which != "all":
            scroll_to_region(new_regions[-1] if which == "next" else new_regions[0], self.view)


class TreeSitterSelectDescendantCommand(sublime_plugin.TextCommand):
    """
    Find node that spans region, then find first descendant that's smaller than this node, and select region
    corresponding to this node.
    """

    def run(self, edit, reverse_sel: bool = True):
        sel = self.view.sel()
        new_region: sublime.Region | None = None

        for region in sel:
            if desc := get_descendant(region, self.view):
                new_region = get_region_from_node(desc, self.view, reverse=reverse_sel)
                sel.subtract(region)
                sel.add(new_region)

        if new_region and len(sel) == 1:
            scroll_to_region(new_region, self.view)


class TreeSitterGotoQueryCommand(sublime_plugin.TextCommand):
    """
    Render goto options in current buffer from tree sitter query, run on node spanned by `region`.

    If query returns no captures, or query file for this language/path doesn't exist, fall back to built-in goto text
    command.
    """

    def fallback(self):
        not_none(self.view.window()).run_command("show_overlay", {"overlay": "goto", "text": "@"})

    def run(self, edit, region: Tuple[int, int] | None = None, query_file: str = SYMBOLS_FILE, queries_path: str = ""):
        if not (tree_dict := get_tree_dict(self.view.buffer_id())):
            return self.fallback()

        try:
            captures = get_captures_from_nodes([tree_dict["tree"].root_node], self.view)
        except FileNotFoundError:
            pass
        else:
            if captures:
                return goto_captures(captures, self.view)

        self.fallback()


class TreeSitterSelectQueryCommand(sublime_plugin.TextCommand):
    """
    Select captures from tree sitter query run on node spanned by `region`.
    """

    def run(self, edit, region: Tuple[int, int] | None = None, query_file: str = SYMBOLS_FILE, queries_path: str = ""):
        if not (tree_dict := get_tree_dict(self.view.buffer_id())):
            return

        captures = get_captures_from_nodes(
            [tree_dict["tree"].root_node], self.view, query_file=query_file, queries_path=queries_path
        )

        if captures:
            sel = self.view.sel()
            sel.clear()
            for node, _, _, _ in captures:
                sel.add(get_region_from_node(node, self.view))


class TreeSitterPrintTreeCommand(sublime_plugin.TextCommand):
    """
    For debugging. If nothing selected, print syntax tree for buffer. Else, print segment(s) of tree for selection(s).
    """

    def format_node(self, node: Node):
        return f"{node.type}  {node.start_point} → {node.end_point}"

    def run(self, edit):
        indent = " " * 2
        if not (tree_dict := get_tree_dict(self.view.buffer_id())):
            return

        parts: list[str] = []
        for root_node in get_selected_nodes(self.view) or [tree_dict["tree"].root_node]:
            while root_node.parent and get_size(root_node) == get_size(root_node.parent):
                # Move to "shallowest" ancestor with the same size as node spanning region
                root_node = root_node.parent
            parts.extend([f"{indent * depth}{self.format_node(node)}" for node, depth in walk_tree(root_node)])
            parts.append("")

        name = get_view_name(self.view)
        language = get_scope_to_language_name()[tree_dict["scope"]]
        debug_view_name = f"Tree ({language}) - {name}" if name else f"Tree ({language})"
        render_debug_view(self.view, debug_view_name, "\n".join(parts))


class TreeSitterShowNodeUnderSelectionCommand(sublime_plugin.TextCommand):
    """
    For debugging. Render a popup with info about the node under the first cursor/selection.
    """

    def run(self, edit):
        show_node_under_selection(self.view, select=True)


SHOW_NODE_SETTINGS_NAME = "tree_sitter.show_node_under_selection"


class TreeSitterToggleShowNodeUnderSelectionCommand(sublime_plugin.TextCommand):
    """
    For debugging, toggle a setting to render a popup with info about the node under the first cursor/selection,
    whenever the selection changes.
    """

    def run(self, edit):
        settings = self.view.settings()

        settings.set(SHOW_NODE_SETTINGS_NAME, not bool(settings.get(SHOW_NODE_SETTINGS_NAME, False)))


class TreeSitterOnSelectionModifiedListener(sublime_plugin.EventListener):
    """
    For debugging, accompanies `TreeSitterToggleShowNodeUnderSelectionCommand`.
    """

    def on_selection_modified_async(self, view: sublime.View):
        if view.settings().get(SHOW_NODE_SETTINGS_NAME, False):
            show_node_under_selection(view, select=False)
