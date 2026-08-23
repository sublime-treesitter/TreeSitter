from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypedDict, cast

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
    mutable_settings,
    parse,
    publish_tree_update,
    trim_cached_trees,
)
from .utils import (
    PROJECT_ROOT,
    get_queries_path,
    get_scope_to_language_name,
    get_scope_to_queries_name,
    log,
    maybe_none,
    not_none,
)

if TYPE_CHECKING:
    from tree_sitter import Language, Node, Tree

    from .core import Injection

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
        # `get_tree_dict` can be called synchronously from a command on the main thread; we pass `only_downloaded=True`
        # to ensure computing injections doesn't block on network I/O for a not-yet-cached injected language
        tree = parse(Parser(), scope, view_text)
        BUFFER_ID_TO_TREE[buffer_id] = make_tree_dict(tree, view_text, scope, only_downloaded=True)
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
    parser = Parser(SCOPE_TO_LANGUAGE[validated_scope])
    return parser.parse(s.encode() if isinstance(s, str) else s)


def query_node_with_language(language: Language, node: Node | InjectedNode, query_s: str):
    """
    Query a node with `query_s`, against `language` directly (see `query_node_with_s` for the scope-based version,
    used for a buffer's own top-level language; this one also backs symbol search in injected trees, each of which
    has its own `Language` - see `get_captures_from_injections`). Returns `(node, capture_name)` tuples, in the same
    document order in which their matches were found, i.e. an ancestor's captures always come before its
    descendants'. This ordering is relied on by `get_captures_from_nodes`, e.g. to build symbol breadcrumbs, since a
    node's breadcrumb ancestors must already have been seen by the time the node itself is processed.

    Note `QueryCursor.captures` doesn't preserve this ordering (it groups all captures by capture name), so we build
    this list from `QueryCursor.matches` instead.

    `QueryCursor.matches` needs a real `tree_sitter.Node`, so `node` is unwrapped if it's an `InjectedNode` (see
    `unwrap`).

    See https://github.com/tree-sitter/py-tree-sitter#pattern-matching
    """
    from tree_sitter import Query, QueryCursor

    cursor = QueryCursor(Query(language, query_s))
    return [
        (captured_node, name)
        for _, captures in cursor.matches(unwrap(node))
        for name, nodes in captures.items()
        for captured_node in nodes
    ]


def query_node_with_s(scope: str | None, node: Node | InjectedNode, query_s: str):
    """
    `query_node_with_language`, resolving `Language` from a Sublime scope (see `SCOPE_TO_LANGUAGE`) rather than
    taking one directly.
    """
    if not (scope := check_scope(scope)):
        return
    return query_node_with_language(SCOPE_TO_LANGUAGE[scope], node, query_s)


def get_query_s_from_file(
    queries_name: str,
    queries_path: str | Path = "",
    symbols_file: str = SYMBOLS_FILE,
    ignore_file_not_found: bool = False,
) -> str:
    """
    Handle `inherits` "pragmas" of the following structure: `; inherits: lang(,other_lang)`

    Passing `ignore_file_not_found=True` to recursive calls of this function essentially makes inherits pragma
    not "strict". See https://github.com/sublime-treesitter/TreeSitter/pull/6 for more context.
    """
    INHERITS_PREFIX = "; inherits:"

    queries_path = os.path.expanduser(queries_path or get_queries_path(mutable_settings.d))
    path = Path(queries_path) / queries_name / symbols_file

    names: list[str] = []
    try:
        with open(path, "r") as f:
            query_s = f.read()
    except FileNotFoundError:
        if not ignore_file_not_found:
            raise
        log(f"query file not found, so it was ignored:\n{path}")
        query_s = ""
    else:
        for line in query_s.splitlines():
            if line.startswith(INHERITS_PREFIX):
                names = [name.strip() for name in line.split(INHERITS_PREFIX)[1].split(",") if name]

    queries = [
        get_query_s_from_file(
            queries_name=name,
            queries_path=queries_path,
            symbols_file=symbols_file,
            ignore_file_not_found=True,
        )
        for name in names
    ]
    return "\n".join([query_s, *queries])


def get_tags_query_s(language_name: str) -> str:
    """
    Fall back to the "tags" query `tree_sitter_language_pack` bundles for `language_name`, when this plugin doesn't
    ship (and the user hasn't supplied) a `symbols.scm` for it. This is the community-standard convention for a
    language-agnostic symbol outline (also used by e.g. GitHub's semantic, and ctags-like tooling generally): its
    `@definition.class`/`@definition.function`/... captures already match `CAPTURE_NAME_PREFIX`/`CAPTURE_NAME_TO_KIND`
    here, so goto/select symbol works with no changes, just without breadcrumbs (`tags.scm` has no equivalent of this
    plugin's `@breadcrumb.N` pragma - see `TreeSitterGotoSymbolCommand`).
    """
    try:
        from tree_sitter_language_pack import get_tags_query
    except ImportError:
        return ""
    return get_tags_query(language_name) or ""


def walk_tree(tree_or_node: Tree | Node, max_depth: int | None = None):
    """
    Walk all the nodes under `tree_or_node`.

    See https://github.com/tree-sitter/py-tree-sitter/issues/33#issuecomment-864557166
    """
    cursor = tree_or_node.walk()

    reached_root = False
    while not reached_root:
        yield cursor.node, cursor

        if (max_depth is None or cursor.depth < max_depth) and cursor.goto_first_child():
            # Don't walk children if we've already reached `max_depth`
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


def get_injection_for_node(node: Node, injections: list[Injection]) -> Injection | None:
    """
    Does `node`'s byte range exactly match one of `injections`' root node, i.e. is `node` an `@injection.content` node
    with a tree parsed for it (see `core.compute_injections`)? Doesn't match a node merely contained within one.
    """
    for injection in injections:
        root = injection["tree"].root_node
        if root.start_byte == node.start_byte and root.end_byte == node.end_byte:
            return injection
    return None


def format_injected_tree(
    root_node: Node,
    injections: list[Injection],
    format_node,
    indent: str,
    depth_offset: int = 0,
) -> list[str]:
    """
    Render `root_node`'s tree as indented lines with `format_node(node, field_name)`, one per node, splicing in each
    injected tree in `injections` (see `core.compute_injections`) right after the node it was injected into.

    Used by `TreeSitterPrintTreeCommand`.
    """
    lines: list[str] = []
    for n, cursor in walk_tree(root_node):
        node = not_none(n)
        depth = cursor.depth + depth_offset
        lines.append(f"{indent * depth}{format_node(node, cursor.field_name)}")

        if injection := get_injection_for_node(node, injections):
            lines.append(f"{indent * (depth + 1)}[injected: {injection['language_name']}]")
            lines.extend(
                format_injected_tree(
                    injection["tree"].root_node, injection["children"], format_node, indent, depth_offset=depth + 2
                )
            )

    return lines


def descendant_for_byte_range(node: Node, start_byte: int, end_byte: int) -> Node | None:
    """
    Get the smallest node within the given byte range.

    This API added in September 2023: https://github.com/tree-sitter/py-tree-sitter/pull/150/files

    See also: https://tree-sitter.github.io/tree-sitter/using-parsers#named-vs-anonymous-nodes
    """
    return node.descendant_for_byte_range(start_byte, end_byte)


class InjectedNode:
    """
    Wraps a `tree_sitter.Node`, so that navigating via `.parent`/`.children` can cross injection boundaries (see
    `core.compute_injections`) as if the outer tree and every tree injected into it (transitively) were one tree.

    This exists because `tree_sitter.Tree`/`Node` can't literally be merged across languages: they're immutable
    native structures, one per `Language`, and there's no API for a `Node` in one `Tree` to have a `.parent` living in
    a different `Tree`. So instead, this is the "glue": `.children` descends into an injected tree if a child is
    exactly an `@injection.content` node (see `get_injection_for_node`), and `.parent` climbs back out to the outer
    node an injected tree's root replaced, once the wrapped node's own native `.parent` is exhausted.

    Every other attribute (`.type`, `.start_byte`, `.text`, `.id`, `.child_by_field_name`, ...) is delegated straight
    to the wrapped node, unchanged - use `.raw` to get it directly, e.g. to run a `Query` against it (`QueryCursor`
    needs a real `tree_sitter.Node`, not this wrapper).

    Deliberately not used by anything that needs to walk a whole subtree (`walk_tree`, `get_cousins`, symbol queries):
    for those, wrapping every visited node is wasted overhead, and (for `get_cousins`) crossing isn't even wanted -
    see `core.compute_injections`'s module docstring for which commands cross injection boundaries and which don't.
    """

    __slots__ = ("_injections", "_outer", "raw")

    def __init__(self, node: Node, injections: list[Injection], outer: InjectedNode | None = None):
        self.raw = node
        self._injections = injections
        self._outer = outer

    @property
    def parent(self) -> InjectedNode | None:
        if (parent := self.raw.parent) is not None:
            return InjectedNode(parent, self._injections, self._outer)
        return self._outer

    @property
    def children(self) -> list[InjectedNode]:
        return [self._wrap_child(c) for c in self.raw.children]

    def _wrap_child(self, child: Node) -> InjectedNode:
        if injection := get_injection_for_node(child, self._injections):
            outer = InjectedNode(child, self._injections, self._outer)
            return InjectedNode(injection["tree"].root_node, injection["children"], outer)
        return InjectedNode(child, self._injections, self._outer)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.raw, name)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, InjectedNode) and self.raw == other.raw

    def __hash__(self) -> int:
        return hash(self.raw.id)

    def __repr__(self) -> str:
        return f"InjectedNode({self.raw!r})"


def unwrap(node: Node | InjectedNode) -> Node:
    """
    Get the plain `tree_sitter.Node` underneath `node`, whether or not it's an `InjectedNode`.
    """
    return node.raw if isinstance(node, InjectedNode) else node


def resolve_node_for_range(
    root: Node, injections: list[Injection], start_byte: int, end_byte: int, outer: InjectedNode | None = None
) -> InjectedNode | None:
    """
    Find the smallest node spanning `[start_byte, end_byte)` starting from `root`, descending into an injected tree
    (see `core.compute_injections`) whenever one contains the range, rather than returning a node from the outer,
    pre-injection tree (e.g. an unparsed token inside Markdown's `inline` node). Wraps the result in an `InjectedNode`
    so further navigation (`.parent`, `.children`) can cross back out.
    """
    entered = next(
        (
            i
            for i in injections
            if i["tree"].root_node.start_byte <= start_byte <= end_byte <= i["tree"].root_node.end_byte
        ),
        None,
    )
    if entered is not None:
        injection_root = entered["tree"].root_node
        content_node = not_none(descendant_for_byte_range(root, injection_root.start_byte, injection_root.end_byte))
        return resolve_node_for_range(
            injection_root, entered["children"], start_byte, end_byte, InjectedNode(content_node, injections, outer)
        )

    if (node := descendant_for_byte_range(root, start_byte, end_byte)) is None:
        return None
    return InjectedNode(node, injections, outer)


def get_ancestors[NodeT: "Node | InjectedNode"](node: NodeT, max_len: int | None = None) -> list[NodeT]:
    """
    Get all ancestors of node, including node itself.

    Works with a plain `tree_sitter.Node` (climbing via its native `.parent`, i.e. never crossing injection
    boundaries: this is what `get_cousins` wants) or an `InjectedNode` (climbing via its `.parent`, which does cross
    boundaries: this is what everything else here wants). See `InjectedNode`.

    Untyped internally (`.parent` climbs within whichever of the two types `node` actually is, but the two aren't
    related closely enough for a type checker to track that polymorphism through a loop); the return type is honest.
    """
    nodes: list[Any] = []
    current_node: Any = node

    while current_node:
        nodes.append(current_node)
        current_node = current_node.parent
        if max_len is not None and len(nodes) >= max_len:
            break
    return nodes


def get_depth(node: Node | InjectedNode) -> int:
    """
    Get 0-based depth of node relative to tree's `root_node`. See `get_ancestors` for `Node` vs. `InjectedNode`.
    """
    return len(get_ancestors(node)) - 1


def get_node_spanning_region(region: sublime.Region | tuple[int, int], buffer_id: int) -> InjectedNode | None:
    """
    Get smallest node spanning region, s.t. node's start point is less than or equal to region's start point, and
    node's end point is greater than or equal region's end point. Descends into injected trees (see
    `core.compute_injections`) when the region falls inside one, e.g. a point inside a Markdown fenced Python block
    resolves to a node in the injected Python tree, not the Markdown `code_fence_content` leaf: see `InjectedNode`.

    If there are two nodes matching a zero-width region, prefer the "deeper" of the two, i.e. the one furthest from the
    root of the tree.
    """
    if not (tree_dict := get_tree_dict(buffer_id)):
        return None

    region = region if isinstance(region, sublime.Region) else sublime.Region(*region)
    root_node = tree_dict["tree"].root_node
    injections = tree_dict["injections"]
    s = tree_dict["s"]

    desc = resolve_node_for_range(root_node, injections, byte_offset(region.begin(), s), byte_offset(region.end(), s))

    if len(region) == 0:
        other_desc = resolve_node_for_range(
            root_node,
            injections,
            byte_offset(region.begin() - 1, s),
            byte_offset(region.end() - 1, s),
        )
    else:
        return desc

    if desc and other_desc:
        # If there are two nodes that match this region, prefer the "deeper" of the two
        return desc if len(get_ancestors(desc)) >= len(get_ancestors(other_desc)) else other_desc
    return None


def get_region_from_node(
    node: Node | InjectedNode, buffer_id_or_view: int | sublime.View, reverse=False
) -> sublime.Region:
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


def contains(a: Node, b: Node) -> bool:
    """
    Does node `a` contain `b`?
    """
    return a.start_byte <= b.start_byte and a.end_byte >= b.end_byte


def get_size(node: Node | InjectedNode) -> int:
    """
    Get size of node in bytes.
    """
    return node.end_byte - node.start_byte


def get_larger_ancestor[NodeT: "Node | InjectedNode"](node: NodeT) -> NodeT | None:
    """
    Get "first" ancestor of node that's larger than this node. See `get_ancestors` for `Node` vs. `InjectedNode`, and
    for why this is untyped internally.
    """
    current: Any = node
    while True:
        if not current.parent:
            return None
        if get_size(current.parent) > get_size(current):
            return current.parent
        current = current.parent


def get_ancestor(region: sublime.Region, view: sublime.View) -> InjectedNode | None:
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


def walk_injected_descendants(node: InjectedNode):
    """
    Like `walk_tree`, but via `InjectedNode.children`, so it crosses injection boundaries (see `core.compute_injections`
    and `InjectedNode`). Used by `get_descendant`, which only ever needs the first smaller descendant of a handful of
    nodes, not a whole-tree walk, so the extra wrapping overhead compared to `walk_tree`'s cursor-based traversal
    doesn't matter here.
    """
    yield node
    for child in node.children:
        yield from walk_injected_descendants(child)


def get_descendant(region: sublime.Region, view: sublime.View) -> InjectedNode | None:
    """
    Find node that spans region, then find first descendant that's smaller than this node. This descendant is basically
    guaranteed to have at least one sibling. Crosses injection boundaries (see `core.compute_injections`) via
    `InjectedNode`.
    """
    if not (tree_dict := get_tree_dict(view.buffer_id())):
        return None

    node = get_node_spanning_region(region, view.buffer_id()) or InjectedNode(
        tree_dict["tree"].root_node, tree_dict["injections"]
    )
    for desc in walk_injected_descendants(node):
        if get_size(desc) < get_size(node):
            return desc
    return None


def escape_injection_roots(node: InjectedNode) -> InjectedNode:
    """
    If `node` is (or, after climbing, becomes) the root of an injected tree, substitute the outer node it replaced,
    since an injected tree's root never appears in any `.children` list (unlike every other node, it doesn't occupy a
    position among literal siblings in its own tree) - so it can't be looked up by "sibling position" at all. Applied
    repeatedly, since the outer node it replaced could itself be an injection root (nested injections).
    """
    while node.raw.parent is None and node.parent is not None:
        node = node.parent
    return node


def get_sibling(region: sublime.Region, view: sublime.View, forward: bool = True) -> InjectedNode | None:
    """
    - Find node that spans region
    - Find "first" ancestor of this node, including node itself, that has siblings
        - If node spanning region is root node, find "first" descendant that has siblings
    - Return the next or previous sibling

    Crosses injection boundaries via `InjectedNode` (see `core.compute_injections` and `escape_injection_roots`).
    """
    node = get_node_spanning_region(region, view.buffer_id())
    if not node:
        return None

    node = escape_injection_roots(node)

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
            # `node.parent` might itself be an injected tree's root (e.g. Markdown's `code_fence_content` injects a
            # JSON tree whose root, `document`, has exactly one child) - re-escape after every climb, not just once
            # at the top, or the next step below can look for a sibling position that doesn't exist
            node = escape_injection_roots(node.parent)
        else:
            break

    parent = not_none(node.parent)
    # Find `node`'s position by comparing raw nodes: `parent.children` (wrapped) auto-descends into an injection at
    # `node`'s own position (see `InjectedNode.children`), so `parent.children.index(node)` would never find it there
    idx = parent.raw.children.index(node.raw)
    idx = idx + 1 if forward else idx - 1
    siblings = parent.children
    return siblings[idx % len(siblings)]


WhichCousinsType = Literal["next", "previous", "all"]


def get_cousins(
    region: sublime.Region,
    view: sublime.View,
    *,
    same_types: bool = True,
    same_text: bool = False,
    same_depth: bool = True,
    same_types_depth: int | None = None,
    which: WhichCousinsType = "all",
) -> list[Node]:
    """
    Find node that spans region, and return next/previous/all nodes that:

    - Are at same depth in tree
    - If `same_types` is `True`, have same `type`, and have ancestors of the same `type`s
    - If `same_text` is `True`, have same `text`

    Deliberately doesn't cross injection boundaries (see `core.compute_injections`): "same depth, same type" only
    means something within one grammar, so this operates on the plain `tree_sitter.Node` (see `InjectedNode`), and
    only ever finds cousins within whichever single tree - the outer one, or an injected one - `node` belongs to.
    """
    wrapped = get_node_spanning_region(region, view.buffer_id())
    node = wrapped.raw if wrapped else None
    if not node or not node.parent:
        return []

    ancestors = get_ancestors(node)
    ancestor_types = [ancestor.type for ancestor in ancestors]
    if same_types_depth is not None:
        ancestor_types = ancestor_types[:same_types_depth]
    node_depth = len(ancestors) - 1

    cousins: list[Node] = []
    for cousin, cursor in walk_tree(ancestors[-1], max_depth=node_depth if same_depth else None):
        # Don't touch this code, it's optimized for performance
        cousin = not_none(cousin)
        if same_depth and cursor.depth != node_depth:
            continue
        if same_text and cousin.text != node.text:
            continue
        if same_types:
            cousin_types = [ancestor.type for ancestor in get_ancestors(cousin, same_types_depth)]
            if cousin_types != ancestor_types:
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


def get_selected_nodes(view: sublime.View, include_emtpy_regions: bool = False) -> list[InjectedNode]:
    """
    Get nodes selected in `view`. Crosses injection boundaries via `InjectedNode` (see `core.compute_injections`).
    """
    nodes: list[InjectedNode] = []
    for region in view.sel():
        if include_emtpy_regions or len(region) > 0:
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


def get_field_name(node: Node | InjectedNode) -> str | None:
    """
    Because there's no `Node.field_name` method or similar.
    """
    if not (parent := node.parent):
        return None
    # Compare raw nodes: wrapped `parent.children` auto-descends into an injection at `node`'s own position (see
    # `InjectedNode.children`), so it would never contain something that equals a wrapped `node` in that case
    raw_node, raw_parent = unwrap(node), unwrap(parent)
    for idx, sibling in enumerate(raw_parent.children):
        if sibling.id == raw_node.id:
            return raw_parent.field_name_for_child(idx)
    return None


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

    node = nodes[0]
    pairs: list[tuple[str, str]] = [
        ("type", node.type),
        ("depth", str(get_depth(node))),
        ("range", f"{node.start_point} → {node.end_point}"),
        ("lang", get_scope_to_language_name(mutable_settings.d)[tree_dict["scope"]]),
        ("scope", tree_dict["scope"]),
    ]
    if field_name := get_field_name(node):
        pairs.insert(1, ("field", field_name))

    for node in nodes[1:]:
        pairs.insert(0, ("", "➔"))
        pairs.insert(0, ("depth", str(get_depth(node))))
        if field_name := get_field_name(node):
            pairs.insert(0, ("field", field_name))
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
    "definition.interface",
    "definition.var",
    "definition.constant",
    "definition.object",
    "definition.element",
    "definition.import",
    "definition.function",
    "definition.call",
    "definition.block",
    "definition.if",
    "definition.loop",
    "definition.test",
]

CAPTURE_NAME_TO_KIND: dict[CaptureNameType, sublime.Kind] = {
    "definition.class": (sublime.KindId.TYPE, "c", "c"),
    "definition.type": (sublime.KindId.TYPE, "t", "t"),
    "definition.interface": (sublime.KindId.TYPE, "i", "i"),
    "definition.var": (sublime.KindId.VARIABLE, "v", "v"),
    "definition.constant": (sublime.KindId.VARIABLE, "c", "c"),
    "definition.object": (sublime.KindId.VARIABLE, "o", "o"),
    "definition.element": (sublime.KindId.VARIABLE, "e", "e"),
    "definition.import": (sublime.KindId.COLOR_DARK, "i", "i"),
    "definition.function": (sublime.KindId.FUNCTION, "f", "f"),
    "definition.call": (sublime.KindId.COLOR_ORANGISH, "l", "l"),
    "definition.block": (sublime.KindId.COLOR_DARK, "b", "b"),
    "definition.if": (sublime.KindId.COLOR_DARK, "i", "i"),
    "definition.loop": (sublime.KindId.COLOR_DARK, "l", "l"),
    "definition.test": (sublime.KindId.COLOR_DARK, "t", "t"),
}


CAPTURE_NAME_PREFIX = "definition."
BREADCRUMB_CAPTURE_NAME = "breadcrumb"


def parse_breadcrumb_depth(capture_name: str) -> int:
    """
    For breadcrumb nodes, parse breadcrumb node depth compared with "container" depth.
    """
    parts = capture_name.split(".")
    return int(parts[1]) if len(parts) == 2 else 0


class BreadcrumbDict(TypedDict):
    node: Node
    name: str
    depth: int
    container: Node


class CaptureDict(TypedDict):
    node: Node
    name: str
    breadcrumbs: list[BreadcrumbDict]
    search_node: Node | InjectedNode
    breadcrumb: BreadcrumbDict | None


def get_captures_from_nodes_with_language(
    nodes: list[Node | InjectedNode], language: Language, query_s: str
) -> list[CaptureDict]:
    """
    `get_captures_from_nodes`, querying against `language` directly rather than resolving one from a view's buffer -
    used for symbol search in injected trees, each of which has its own `Language` (see
    `get_captures_from_injections`). Breadcrumbs are only ever built from ancestors within `language`'s own tree
    (`get_ancestors(container)`, not crossing injection boundaries - see `InjectedNode`), so a symbol found inside an
    injected tree gets breadcrumbs from that tree alone, never stitched to the outer document's structure.
    """
    container_id_to_breadcrumb: dict[int, BreadcrumbDict] = {}
    node_id_to_breadcrumb_depth: dict[int, int] = {}
    captures: list[CaptureDict] = []

    for search_node in nodes:
        query_captures = query_node_with_language(language, search_node, query_s)

        # Do a first pass through captured nodes to see which ones are "breadcrumbs"
        for captured_node, capture_name in query_captures or []:
            if capture_name.startswith(BREADCRUMB_CAPTURE_NAME):
                node_id_to_breadcrumb_depth[captured_node.id] = parse_breadcrumb_depth(capture_name)

        for captured_node, capture_name in query_captures or []:
            if capture_name.startswith(BREADCRUMB_CAPTURE_NAME):
                continue

            if not capture_name.startswith(CAPTURE_NAME_PREFIX):
                continue

            breadcrumb: BreadcrumbDict | None = None
            container = captured_node
            if (bc_depth := node_id_to_breadcrumb_depth.get(captured_node.id, None)) is not None:
                for _ in range(bc_depth):
                    container = not_none(container.parent)
                breadcrumb = BreadcrumbDict(node=captured_node, name=capture_name, container=container, depth=bc_depth)
                container_id_to_breadcrumb[container.id] = breadcrumb

            captures.append(
                CaptureDict(
                    node=captured_node,
                    name=capture_name,
                    breadcrumbs=[
                        container_id_to_breadcrumb[a.id]
                        for a in get_ancestors(container)[1:]
                        if a.id in container_id_to_breadcrumb
                    ],
                    search_node=search_node,
                    breadcrumb=breadcrumb,
                )
            )

    return captures


def get_captures_from_nodes(nodes: list[Node | InjectedNode], view: sublime.View, query_s: str) -> list[CaptureDict]:
    """
    Get capture tuples from search nodes, querying against the buffer's own top-level language. Capture tuples
    include captured ancestors for rendering breadcrumbs.

    Doesn't search inside injected trees (see `core.compute_injections`): use `get_all_captures` for that.
    """
    if not (tree_dict := get_tree_dict(view.buffer_id())):
        return []
    return get_captures_from_nodes_with_language(nodes, SCOPE_TO_LANGUAGE[tree_dict["scope"]], query_s)


def get_captures_from_injections(
    injections: list[Injection],
    queries_path: str = "",
    symbols_file: str = SYMBOLS_FILE,
) -> list[CaptureDict]:
    """
    Recursively search every language injected into a tree (transitively - see `core.compute_injections`) for
    symbols, resolving a query per injected language the same way as the top-level tree: a user-supplied or
    bundled `symbols.scm` first (`get_query_s_from_file`), then `tree_sitter_language_pack`'s "tags" query
    (`get_tags_query_s`) if that's unavailable.

    A language with neither is skipped (not every embedded snippet has, or needs, an outline); a query that exists
    but fails - a bad bundled/user query, or one of `tree_sitter_language_pack`'s occasional bad ones (see
    `core.get_injections_query`'s docstring) - is logged and skipped, rather than losing symbols from the rest of the
    tree over one broken injection.
    """
    captures: list[CaptureDict] = []

    for injection in injections:
        language_name = injection["language_name"]
        query_s = get_query_s_from_file(
            language_name, queries_path, symbols_file, ignore_file_not_found=True
        ) or get_tags_query_s(language_name)

        if query_s:
            try:
                captures.extend(
                    get_captures_from_nodes_with_language(
                        [injection["tree"].root_node],
                        injection["tree"].language,
                        query_s,
                    )
                )
            except Exception as e:
                log(f'error querying injected "{language_name}" tree for symbols: {e}')

        captures.extend(get_captures_from_injections(injection["children"], queries_path, symbols_file))

    return captures


def get_all_captures(
    tree_dict,
    view: sublime.View,
    queries_path: str = "",
    symbols_file: str = SYMBOLS_FILE,
    ignore_file_not_found: bool = True,
) -> list[CaptureDict]:
    """
    Get captures for `tree_dict`'s own tree, plus every language injected into it, transitively (see
    `core.compute_injections` and `get_captures_from_injections`). Used by `TreeSitterGotoSymbolCommand` and
    `TreeSitterSelectSymbolsCommand` so both search the whole document, not just its top-level language.
    """
    resolved_queries_path = queries_path or get_queries_path(mutable_settings.d)

    query_s = get_query_s_from_file(
        queries_name=get_scope_to_queries_name(mutable_settings.d)[tree_dict["scope"]],
        queries_path=resolved_queries_path,
        symbols_file=symbols_file,
        ignore_file_not_found=ignore_file_not_found,
    ) or get_tags_query_s(get_scope_to_language_name(mutable_settings.d)[tree_dict["scope"]])

    captures = get_captures_from_nodes([tree_dict["tree"].root_node], view, query_s) if query_s else []
    captures.extend(get_captures_from_injections(tree_dict["injections"], resolved_queries_path, symbols_file))
    return captures


def get_capture_kind(name: str) -> sublime.Kind:
    """
    For rendering `QuickPanelItem`s.
    """
    if name not in CAPTURE_NAME_TO_KIND:
        return (sublime.KindId.AMBIGUOUS, "?", "?")
    return CAPTURE_NAME_TO_KIND[name]


def on_highlight_repaint_view(view: sublime.View):
    """
    Works around ST quick panel rendering bug. Modifying selection in `on_highlight` callback has no effect unless
    viewport moves or its contents change. Notes on the implementation:

    - Unlike using `View.set_viewport_position`, this works even if the buffer contents all fit in the viewport
    - Its only side effect is to unfold any folded region including `Region(0, 1)`
    """
    region = sublime.Region(0, 1)
    view.fold(region)
    view.unfold(region)


def format_node_text(text: str):
    if " " not in text:
        return text
    return " ".join(text.split())


def format_breadcrumbs(breadcrumbs: list[Node]):
    return " > ".join(format_node_text(not_none(a.text).decode()) for a in reversed(breadcrumbs))


def format_capture_name(capture_name: str) -> str:
    parts = capture_name.split(".")
    if len(parts) < 2:
        return ""
    return parts[1].capitalize()


def goto_captures(captures: list[CaptureDict], view: sublime.View):
    """
    Render goto options in quick panel in `view`, from list of `captures`. Captures can be gotten with
    `get_captures_from_nodes`.
    """
    options: list[sublime.QuickPanelItem] = []
    for capture in captures:
        breadcrumbs = capture["breadcrumbs"]
        options.append(
            sublime.QuickPanelItem(
                trigger=f"{'. ' * len(breadcrumbs)}{format_node_text(not_none(capture['node'].text).decode())}",
                kind=get_capture_kind(capture["name"]),
                details=format_breadcrumbs([bc["node"] for bc in breadcrumbs]),
                annotation=format_capture_name(capture["name"]),
            )
        )

    goto_capture_options(captures, options, view)


def goto_capture_options(
    captures: list[CaptureDict],
    options: list[sublime.QuickPanelItem],
    view: sublime.View,
    filter_by_capture_name: str | None = None,
):
    """
    Separate from `goto_captures` so that users can easily write their own `goto_captures`, e.g. for custom rendering of
    goto options.
    """
    filter_option = sublime.QuickPanelItem(
        trigger="?", kind=(sublime.KindId.AMBIGUOUS, "", ""), details="Select symbol type"
    )

    if filter_by_capture_name is None:
        current_captures = [cast(CaptureDict, {}), *captures]
        current_options = [filter_option, *options]
    else:
        current_captures = [cast(CaptureDict, {})]
        current_options = [filter_option]
        for idx, capture in enumerate(captures):
            if capture["name"] == filter_by_capture_name:
                current_captures.append(capture)
                current_options.append(options[idx])

    def on_highlight(idx: int):
        """
        Scroll to symbol and select it.
        """
        if idx == 0:  # Not actually a symbol
            return

        node = current_captures[idx]["node"]
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
    selected_index = 1 if len(current_captures) > 1 else 0
    if regions:
        row, _ = view.rowcol(regions[0].begin())
        for idx, capture in enumerate(current_captures):
            if idx > 0 and row >= capture["node"].start_point[0]:
                selected_index = idx

    def on_select(idx: int):
        """
        If user "cancels" selection, revert selection and viewport position to initial values.
        """
        if idx == 0:
            select_capture_name(captures, options, view)

        if idx == -1:
            view.set_viewport_position(xy)
            sel = view.sel()
            sel.clear()
            sel.add_all(regions)

    window = not_none(view.window())
    window.show_quick_panel(
        current_options, on_select=on_select, on_highlight=on_highlight, selected_index=max(selected_index, 0)
    )


def select_capture_name(captures: list[CaptureDict], options: list[sublime.QuickPanelItem], view: sublime.View):
    """
    - Get unique capture names from captures
    - Prompt user to select a name, or all capture names
    - Call `goto_capture_options` with selected name to filter down capture options
    """
    capture_names: set[str] = set()
    for capture in captures:
        capture_names.add(capture["name"])

    all_types_option = sublime.QuickPanelItem(trigger="All types", kind=(sublime.KindId.AMBIGUOUS, "", ""))
    capture_name_options: list[sublime.QuickPanelItem] = [all_types_option]

    capture_names_list = sorted(capture_names)
    for name in sorted(capture_names_list):
        option = sublime.QuickPanelItem(trigger=format_capture_name(name), kind=get_capture_kind(name))
        capture_name_options.append(option)

    def on_select(idx: int):
        if idx == -1:
            return

        capture_name = None if idx == 0 else capture_names_list[idx - 1]
        goto_capture_options(captures, options, view, capture_name)

    window = not_none(view.window())
    window.show_quick_panel(capture_name_options, on_select=on_select, selected_index=0)


#
# Select, goto and debug commands
#


class TreeSitterReloadCommand(sublime_plugin.ApplicationCommand):
    """
    Reload the plugin.
    """

    def run(self):
        root_file = Path(PROJECT_ROOT) / "load.py"
        root_file.touch()


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

        if extend:
            # Perf optimization for extending selection, no need to get_sibling for all selected regions
            regions = [sel[-1] if forward else sel[0]]
        else:
            regions = sel

        for region in regions:
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
        same_depth: bool = True,
        same_types_depth: int | None = None,
        which: WhichCousinsType = "all",
        extend: bool = False,
        reverse_sel: bool = True,
    ):
        sel = self.view.sel()
        new_regions: list[sublime.Region] = []

        if extend:
            # Perf optimization for extending selection, no need to get_cousins for all selected regions
            regions = [sel[-1] if which == "next" else sel[0]]
        else:
            regions = sel

        for region in regions:
            for cousin in get_cousins(
                region,
                self.view,
                same_types=same_types,
                same_text=same_text,
                same_depth=same_depth,
                same_types_depth=same_types_depth,
                which=which,
            ):
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


class TreeSitterSelectSymbolsCommand(sublime_plugin.TextCommand):
    """
    Select symbol from current buffer captured by tree sitter query, including symbols in every language injected
    into it (transitively - see `core.compute_injections` and `get_all_captures`).

    If this plugin doesn't ship (and the user hasn't supplied) a `symbols.scm` for a given language (the buffer's own,
    or an injected one), falls back to the "tags" query `tree_sitter_language_pack` bundles for it: see
    `get_tags_query_s`.
    """

    def run(
        self,
        edit,
        region: tuple[int, int] | None = None,
        queries_path: str = "",
        symbols_file: str = SYMBOLS_FILE,
        ignore_file_not_found=True,
    ):
        if not (tree_dict := get_tree_dict(self.view.buffer_id())):
            return

        captures = get_all_captures(tree_dict, self.view, queries_path, symbols_file, ignore_file_not_found)
        if captures:
            sel = self.view.sel()
            sel.clear()
            for capture in captures:
                sel.add(get_region_from_node(capture["node"], self.view))


class TreeSitterGotoSymbolCommand(sublime_plugin.TextCommand):
    """
    Render goto options for symbols in current buffer captured by tree sitter query, including symbols in every
    language injected into it (transitively - see `core.compute_injections` and `get_all_captures`).

    If this plugin doesn't ship (and the user hasn't supplied) a `symbols.scm` for a given language (the buffer's own,
    or an injected one), falls back to the "tags" query `tree_sitter_language_pack` bundles for it (see
    `get_tags_query_s`); if that's unavailable either, or no captures turn up anywhere, falls back to Sublime's own
    built-in goto command.

    Symbols found inside an injected tree only ever get breadcrumbs from within that same tree, never stitched to the
    outer document's structure - see `get_captures_from_nodes_with_language`.

    TODO: our own `symbols.scm` convention diverges from the community-standard "tags" convention `get_tags_query_s`
    sources from (also used by e.g. GitHub's semantic, and ctags-like tooling generally): our `@definition.<kind>` goes
    on the identifier plus an explicit `@breadcrumb.N` pragma to find its container, where the standard convention puts
    `@definition.<kind>` on the whole definition node, with a separate `@name` for the identifier, and no breadcrumb
    pragma at all (breadcrumbs fall out structurally, since a definition's container is just its nearest ancestor
    that's also a definition - no depth counting needed). Worth eventually reconciling: dropping `@breadcrumb.N` in
    favor of that structural approach if it reproduces today's breadcrumb behavior, renaming `symbols.scm` files to
    `tags.scm`, and making use of `@name`/`@reference.call` where it'd help (e.g. find-references).
    """

    def fallback(self):
        not_none(self.view.window()).run_command("show_overlay", {"overlay": "goto", "text": "@"})

    def run(
        self,
        edit,
        region: tuple[int, int] | None = None,
        queries_path: str = "",
        symbols_file: str = SYMBOLS_FILE,
        ignore_file_not_found=True,
    ):
        if not (tree_dict := get_tree_dict(self.view.buffer_id())):
            return self.fallback()

        captures = get_all_captures(tree_dict, self.view, queries_path, symbols_file, ignore_file_not_found)
        if captures:
            return goto_captures(captures, self.view)

        self.fallback()


class TreeSitterQuerySymbolCommand(sublime_plugin.WindowCommand):
    """
    Prompt user to input a query string, and search buffer for symbols matching query string. Mainly for debugging. Doesn't search injected trees.
    """

    query_s = ""  # So this can be cached and reused

    def run(self):
        if not (view := self.window.active_view()) or not get_tree_dict(view.buffer_id()):
            return  # No point in prompting user for input if we can't parse buffer
        self.window.show_input_panel("query:", self.query_s, self.on_done, None, None)

    def on_done(self, query_s: str):
        if not (view := self.window.active_view()) or not (tree_dict := get_tree_dict(view.buffer_id())):
            return
        # Transform query_s, e.g. in case it's missing capture name
        if "@" not in query_s:
            query_s = f"(({query_s}) @definition.query)"
        self.query_s = query_s

        nodes = cast("list[Node | InjectedNode]", get_selected_nodes(view)) or [tree_dict["tree"].root_node]
        if captures := get_captures_from_nodes(nodes, view, query_s):
            goto_captures(captures, view)


class TreeSitterPrintTreeCommand(sublime_plugin.TextCommand):
    """
    For debugging. If nothing selected, print syntax tree for buffer. Else, print segment(s) of tree for selection(s).
    """

    def format_node(self, node: Node, field_name: str | None = None):
        if field_name:
            return f"{node.type} [{field_name}]  {node.start_point} → {node.end_point}"
        return f"{node.type}  {node.start_point} → {node.end_point}"

    def run(self, edit):
        indent = " " * 2
        if not (tree_dict := get_tree_dict(self.view.buffer_id())):
            return

        parts: list[str] = []
        # `format_injected_tree` splices in injected trees itself (see `get_injection_for_node`), so it wants plain
        # `tree_sitter.Node`s, not `InjectedNode`s
        for root_node in [n.raw for n in get_selected_nodes(self.view)] or [tree_dict["tree"].root_node]:
            while root_node.parent and get_size(root_node) == get_size(root_node.parent):
                # Move to "shallowest" ancestor with the same size as node spanning region
                root_node = root_node.parent
            parts.extend(format_injected_tree(root_node, tree_dict["injections"], self.format_node, indent))
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
