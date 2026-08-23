from __future__ import annotations

import time
import types

import tree_sitter_language_pack as tslp
from tree_sitter import Parser
from tree_sitter_language_pack import get_language

from src import api, core
from src.utils import not_none

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


def test_get_all_captures_finds_symbols_in_injected_python_with_breadcrumbs(monkeypatch):
    """
    `python` has a bundled `queries/python/symbols.scm` (checked first, ahead of tree_sitter_language_pack's "tags"
    query), which has `@breadcrumb.N` pragmas - so a symbol found inside an injected Python tree gets breadcrumbs from
    that tree, exactly as if it were a real top-level Python buffer.
    """
    tree_dict = _make_markdown_tree_dict(monkeypatch)
    fake_view = types.SimpleNamespace(buffer_id=lambda: 1)

    captures = api.get_all_captures(tree_dict, fake_view)

    by_text = {c["node"].text: c for c in captures}
    assert by_text[b"Greeter"]["name"] == "definition.class"
    assert by_text[b"greet"]["name"] == "definition.function"
    assert [b["node"].text for b in by_text[b"greet"]["breadcrumbs"]] == [b"Greeter"]


def test_get_all_captures_falls_back_to_tags_query_for_injected_go(monkeypatch):
    """
    `go` has no bundled `symbols.scm`, but tree_sitter_language_pack ships a "tags" query for it - so this still finds
    the function, just without breadcrumbs (see `get_tags_query_s`).
    """
    tree_dict = _make_markdown_tree_dict(monkeypatch)
    fake_view = types.SimpleNamespace(buffer_id=lambda: 1)

    captures = api.get_all_captures(tree_dict, fake_view)

    # Unlike this plugin's own symbols.scm convention, the go tags query captures the whole function_declaration
    # node under @definition.function, not just its name
    go_functions = [c for c in captures if c["name"] == "definition.function" and b"Add" in (c["node"].text or b"")]
    assert len(go_functions) == 1
    assert go_functions[0]["breadcrumbs"] == []


def test_get_all_captures_skips_injected_language_with_no_query(monkeypatch):
    # `toml` has neither a bundled `symbols.scm` nor a tree_sitter_language_pack "tags" query
    tree_dict = _make_markdown_tree_dict(monkeypatch)
    fake_view = types.SimpleNamespace(buffer_id=lambda: 1)

    captures = api.get_all_captures(tree_dict, fake_view)

    assert not any(b"section" in (c["node"].text or b"") for c in captures)


def test_get_all_captures_skips_broken_injected_query_without_losing_others(monkeypatch):
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

    captures = api.get_all_captures(tree_dict, fake_view)

    assert not any(b"Add" in (c["node"].text or b"") for c in captures)  # the broken "go" query is skipped...
    assert any(c["node"].text == b"Greeter" for c in captures)  # ...but python symbols still come through
