from __future__ import annotations

import time

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
