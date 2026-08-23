from __future__ import annotations

import time
import types

import tree_sitter_language_pack as tslp
from tree_sitter import Parser
from tree_sitter_language_pack import get_language

from src import api, core
from src.utils import get_scope_to_language_name, get_scope_to_queries_name, not_none

MARKDOWN_CODE = b"""\
This is `code`, <https://abc.com>, [link](https://abc.com)

```python
x = 1
```
"""


def _parse(language_name: str, code: bytes):
    language = get_language(language_name)
    root = Parser(language).parse(code).root_node
    return language, root


def test_compute_injections_finds_fenced_code_and_inline():
    language, root = _parse("markdown", MARKDOWN_CODE)
    injections = core.compute_injections(root, language, "markdown", MARKDOWN_CODE, only_downloaded=False)

    by_language = {i["language_name"]: i for i in injections}
    assert set(by_language) == {"markdown_inline", "python"}

    # Byte ranges come out relative to the *outer* buffer, not the injected region, thanks to `included_ranges`
    python_root = by_language["python"]["tree"].root_node
    assert MARKDOWN_CODE[python_root.start_byte : python_root.end_byte] == b"x = 1\n"
    assert python_root.type == "module"
    assert not_none(python_root.child(0)).type == "assignment"

    inline_root = by_language["markdown_inline"]["tree"].root_node
    inline_types = {not_none(n).type for n, _ in api.walk_tree(inline_root)}
    assert "code_span" in inline_types
    assert "inline_link" in inline_types


def test_compute_injections_resolves_fence_language_aliases():
    code = b"```js\nlet x = 1;\n```\n"
    language, root = _parse("markdown", code)
    injections = core.compute_injections(root, language, "markdown", code, only_downloaded=False)

    assert len(injections) == 1
    assert injections[0]["language_name"] == "javascript"
    assert injections[0]["tree"].root_node.type == "program"


def test_compute_injections_no_query_returns_empty_list():
    # `python`'s grammar doesn't ship an injections query
    language, root = _parse("python", b"x = 1\n")
    assert core.compute_injections(root, language, "python", b"x = 1\n", only_downloaded=False) == []


def test_compute_injections_unresolvable_fence_language_is_skipped():
    code = b"```not-a-real-language\nwhatever\n```\n"
    language, root = _parse("markdown", code)
    injections = core.compute_injections(root, language, "markdown", code, only_downloaded=False)
    assert injections == []


def test_get_injections_query_is_cached():
    language = get_language("markdown")
    q1 = core.get_injections_query(language, "markdown")
    q2 = core.get_injections_query(language, "markdown")
    assert q1 is q2
    assert q1 is not None


def test_format_injected_tree_splices_injections_at_the_right_node():
    language, root = _parse("markdown", MARKDOWN_CODE)
    injections = core.compute_injections(root, language, "markdown", MARKDOWN_CODE, only_downloaded=False)

    def format_node(node, field_name=None):
        return node.type

    lines = api.format_injected_tree(root, injections, format_node, indent="  ")
    text = "\n".join(lines)

    assert "[injected: markdown_inline]" in text
    assert "[injected: python]" in text
    assert "code_span" in text
    assert "assignment" in text

    # The injected python tree's own lines should be indented deeper than the `[injected: python]` marker line
    marker_indent = next(len(line) - len(line.lstrip(" ")) for line in lines if "[injected: python]" in line)
    module_indent = next(len(line) - len(line.lstrip(" ")) for line in lines if line.strip() == "module")
    assert module_indent > marker_indent


def _wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    return predicate()


def test_download_injected_language_in_background_dedupes_in_flight_downloads(monkeypatch):
    """
    Two `only_downloaded=True` misses for the same not-yet-cached language (e.g. two keystrokes in quick succession)
    should spawn at most one download thread, not one per call.
    """
    name = "__test_dedup_language__"
    monkeypatch.setattr(core, "INJECTED_LANGUAGES_DOWNLOADING", {name})

    def fail_if_called(*args, **kwargs):
        raise AssertionError("shouldn't spawn a thread for a download already in flight")

    monkeypatch.setattr(core, "Thread", fail_if_called)

    core.download_injected_language_in_background(name)  # no-op: `name` is already "in flight"


def test_get_injected_language_only_downloaded_self_heals_in_background(monkeypatch):
    """
    `only_downloaded=True` shouldn't require the user to explicitly install an injected language they merely
    encountered: a miss should still resolve on its own shortly after, via a background download.
    """
    name = "ruby"
    monkeypatch.setattr(core, "LANGUAGE_NAME_TO_INJECTED_LANGUAGE", {})
    monkeypatch.setattr(core, "INJECTED_LANGUAGES_DOWNLOADING", set())
    # Force the "not cached" branch regardless of this machine's real `tree_sitter_language_pack` cache state
    monkeypatch.setattr(tslp, "downloaded_languages", list)

    assert core.get_injected_language(name, only_downloaded=True) is None

    assert _wait_until(lambda: name in core.LANGUAGE_NAME_TO_INJECTED_LANGUAGE)
    assert _wait_until(lambda: name not in core.INJECTED_LANGUAGES_DOWNLOADING)


MARKDOWN_WITH_SYMBOLS = """# Doc

```python
class Greeter:
    def greet(self):
        pass
```

```go
func Add(a int, b int) int {
    return a + b
}
```

```toml
[section]
key = "value"
```
"""


def _make_markdown_tree_dict(monkeypatch, code: str = MARKDOWN_WITH_SYMBOLS):
    tree = core.parse(Parser(), "text.html.markdown", code)
    tree_dict = core.make_tree_dict(tree, code, "text.html.markdown")
    monkeypatch.setattr(api, "get_tree_dict", lambda buffer_id: tree_dict)
    return tree_dict


def _resolve_query_s(tree_dict) -> str:
    """
    Mirrors the query resolution `TreeSitterGotoSymbolCommand`/`TreeSitterSelectSymbolsCommand` do inline before
    calling `get_captures_from_nodes`: a bundled/user `symbols.scm` first, else `tree_sitter_language_pack`'s "tags"
    query.
    """
    return api.get_query_s_from_file(
        get_scope_to_queries_name()[tree_dict["scope"]], ignore_file_not_found=True
    ) or api.get_tags_query_s(get_scope_to_language_name()[tree_dict["scope"]])


def test_get_captures_from_nodes_finds_symbols_in_injected_python_with_breadcrumbs(monkeypatch):
    """
    `python` has a bundled `queries/python/symbols.scm` (checked first, ahead of tree_sitter_language_pack's "tags"
    query), which has `@breadcrumb.N` pragmas - so a symbol found inside an injected Python tree gets breadcrumbs from
    that tree, exactly as if it were a real top-level Python buffer.
    """
    tree_dict = _make_markdown_tree_dict(monkeypatch)
    fake_view = types.SimpleNamespace(buffer_id=lambda: 1)

    captures = api.get_captures_from_nodes([tree_dict["tree"].root_node], fake_view, _resolve_query_s(tree_dict))

    by_text = {c["node"].text: c for c in captures}
    assert by_text[b"Greeter"]["name"] == "definition.class"
    assert by_text[b"greet"]["name"] == "definition.function"
    assert [b["node"].text for b in by_text[b"greet"]["breadcrumbs"]] == [b"Greeter"]


def test_get_captures_from_nodes_falls_back_to_tags_query_for_injected_go(monkeypatch):
    """
    `go` has no bundled `symbols.scm`, but tree_sitter_language_pack ships a "tags" query for it - so this still finds
    the function, just without breadcrumbs (see `get_tags_query_s`).
    """
    tree_dict = _make_markdown_tree_dict(monkeypatch)
    fake_view = types.SimpleNamespace(buffer_id=lambda: 1)

    captures = api.get_captures_from_nodes([tree_dict["tree"].root_node], fake_view, _resolve_query_s(tree_dict))

    # Unlike this plugin's own symbols.scm convention, the go tags query captures the whole function_declaration
    # node under @definition.function, not just its name
    go_functions = [c for c in captures if c["name"] == "definition.function" and b"Add" in (c["node"].text or b"")]
    assert len(go_functions) == 1
    assert go_functions[0]["breadcrumbs"] == []


def test_get_captures_from_nodes_skips_injected_language_with_no_query(monkeypatch):
    # `toml` has neither a bundled `symbols.scm` nor a tree_sitter_language_pack "tags" query
    tree_dict = _make_markdown_tree_dict(monkeypatch)
    fake_view = types.SimpleNamespace(buffer_id=lambda: 1)

    captures = api.get_captures_from_nodes([tree_dict["tree"].root_node], fake_view, _resolve_query_s(tree_dict))

    assert not any(b"section" in (c["node"].text or b"") for c in captures)


def test_get_captures_from_nodes_skips_broken_injected_query_without_losing_others(monkeypatch):
    """
    A bad query for one injected language (this happens in practice - see core.get_injections_query's docstring)
    shouldn't lose symbols from the rest of the tree.
    """
    tree_dict = _make_markdown_tree_dict(monkeypatch)
    fake_view = types.SimpleNamespace(buffer_id=lambda: 1)

    real_get_tags_query_s = api.get_tags_query_s
    monkeypatch.setattr(
        api,
        "get_tags_query_s",
        lambda name: "(not valid tree-sitter query" if name == "go" else real_get_tags_query_s(name),
    )

    captures = api.get_captures_from_nodes([tree_dict["tree"].root_node], fake_view, _resolve_query_s(tree_dict))

    assert not any(b"Add" in (c["node"].text or b"") for c in captures)  # the broken "go" query is skipped...
    assert any(c["node"].text == b"Greeter" for c in captures)  # ...but python symbols still come through


MARKDOWN_HEADINGS = """# H1

## H2a

### H3a

#### H4a

## H2b

### H3b
"""


def test_get_captures_from_nodes_indexes_markdown_headings_with_structural_breadcrumbs(monkeypatch):
    """
    Headings nest by number and document order (see `compute_heading_breadcrumbs`), without this plugin's own
    `@breadcrumb.N` pragma (queries/markdown/symbols.scm deliberately has none) - multi-level nesting (h4 under h3
    under h2 under h1) falls out for free, and a sibling heading (the second h2) doesn't nest under the first one's
    subtree.
    """
    tree_dict = _make_markdown_tree_dict(monkeypatch, MARKDOWN_HEADINGS)
    fake_view = types.SimpleNamespace(buffer_id=lambda: 1)

    captures = api.get_captures_from_nodes([tree_dict["tree"].root_node], fake_view, _resolve_query_s(tree_dict))
    by_text = {c["node"].text: c for c in captures}

    assert {c["name"] for c in captures} == {"definition.h1", "definition.h2", "definition.h3", "definition.h4"}

    assert by_text[b"H1"]["breadcrumbs"] == []
    assert [b["node"].text for b in by_text[b"H2a"]["breadcrumbs"]] == [b"H1"]
    assert [b["node"].text for b in by_text[b"H3a"]["breadcrumbs"]] == [b"H2a", b"H1"]
    assert [b["node"].text for b in by_text[b"H4a"]["breadcrumbs"]] == [b"H3a", b"H2a", b"H1"]

    # The second h2 is a *sibling* of the first, not nested under H3a/H4a
    assert [b["node"].text for b in by_text[b"H2b"]["breadcrumbs"]] == [b"H1"]
    assert [b["node"].text for b in by_text[b"H3b"]["breadcrumbs"]] == [b"H2b", b"H1"]

    # Every heading is itself a valid breadcrumb container (any heading can have nested subheadings)
    h2a_breadcrumb = not_none(by_text[b"H2a"]["breadcrumb"])
    assert h2a_breadcrumb["node"].text == b"H2a"
    assert h2a_breadcrumb["container"].type == "section"
    assert api.contains(h2a_breadcrumb["container"], by_text[b"H3a"]["node"])
    assert api.contains(h2a_breadcrumb["container"], by_text[b"H4a"]["node"])
    assert not api.contains(h2a_breadcrumb["container"], by_text[b"H2b"]["node"])


def test_get_captures_from_nodes_handles_skipped_heading_levels(monkeypatch):
    # h3 directly under h1, with no h2 in between, should still attach to h1
    code = "# H1\n\n### H3\n"
    tree_dict = _make_markdown_tree_dict(monkeypatch, code)
    fake_view = types.SimpleNamespace(buffer_id=lambda: 1)

    captures = api.get_captures_from_nodes([tree_dict["tree"].root_node], fake_view, _resolve_query_s(tree_dict))
    by_text = {c["node"].text: c for c in captures}

    assert [b["node"].text for b in by_text[b"H3"]["breadcrumbs"]] == [b"H1"]


def test_get_captures_from_nodes_sorts_results_by_buffer_position(monkeypatch):
    """
    Results come from several trees (the outer markdown one, plus one injected tree per fenced code block), resolved
    and appended in whatever order `get_captures_from_injections` recurses through them - so they need an explicit
    sort to end up in the order a goto-symbol quick panel should list them: the order they appear in the buffer.
    """
    tree_dict = _make_markdown_tree_dict(monkeypatch)
    fake_view = types.SimpleNamespace(buffer_id=lambda: 1)

    captures = api.get_captures_from_nodes([tree_dict["tree"].root_node], fake_view, _resolve_query_s(tree_dict))
    start_bytes = [c["node"].start_byte for c in captures]

    assert start_bytes == sorted(start_bytes)
    # Sanity-check this isn't trivially true because there's only one capture per tree: "Doc" (the heading) comes
    # first, then the two symbols from the injected Python tree, then the injected Go function
    by_text = {c["node"].text: c for c in captures}
    assert [t for t in (b"Doc", b"Greeter", b"greet") if t in by_text] == [b"Doc", b"Greeter", b"greet"]


def test_get_captures_from_nodes_scopes_injection_search_to_the_given_node(monkeypatch):
    """
    Passing a specific node (rather than the buffer's root) only searches injections *underneath* that node - here,
    the Python fenced code block - not every injection in the whole document.
    """
    tree_dict = _make_markdown_tree_dict(monkeypatch)
    fake_view = types.SimpleNamespace(buffer_id=lambda: 1)

    python_block = next(
        not_none(n) for n, _ in api.walk_tree(tree_dict["tree"].root_node) if not_none(n).type == "fenced_code_block"
    )

    captures = api.get_captures_from_nodes([python_block], fake_view, _resolve_query_s(tree_dict))
    by_text = {c["node"].text: c for c in captures}

    assert by_text[b"Greeter"]["name"] == "definition.class"
    assert by_text[b"greet"]["name"] == "definition.function"
    assert b"Add" not in by_text  # go's fenced block is a sibling, not underneath the python one
    assert b"Doc" not in by_text  # neither is the outer heading


def test_get_captures_from_nodes_resolves_own_query_for_a_node_already_inside_an_injection(monkeypatch):
    """
    A starting node can itself already be inside an injected tree (see `InjectedNode.own_injection`) - e.g. one
    `get_selected_nodes` returns for a selection made inside a Python fenced code block in a Markdown buffer. The
    caller's `query_s` was resolved for the buffer's own top-level language (Markdown), so it doesn't apply to this
    node - `get_captures_from_nodes` must resolve and use a Python query instead, exactly as if this were a real
    top-level Python buffer.
    """
    tree_dict = _make_markdown_tree_dict(monkeypatch)
    fake_view = types.SimpleNamespace(buffer_id=lambda: 1)

    greeter_start = MARKDOWN_WITH_SYMBOLS.index("Greeter")
    leaf = api.resolve_node_for_range(
        tree_dict["tree"].root_node, tree_dict["injections"], greeter_start, greeter_start + len("Greeter")
    )
    node = not_none(leaf)
    while (parent := node.parent) is not None and parent.own_injection is not None:
        node = parent
    assert node.own_injection is not None and node.own_injection["language_name"] == "python"

    # A markdown query_s (or garbage) must be ignored in favor of a query resolved for the node's own language
    captures = api.get_captures_from_nodes([node], fake_view, "(not a valid query at all (((")
    by_text = {c["node"].text: c for c in captures}

    assert by_text[b"Greeter"]["name"] == "definition.class"
    assert by_text[b"greet"]["name"] == "definition.function"


def test_get_capture_kind_for_headings():
    import sublime

    for level in range(1, 7):
        kind = api.get_capture_kind(f"definition.h{level}")
        assert kind[0] == sublime.KindId.MARKUP
