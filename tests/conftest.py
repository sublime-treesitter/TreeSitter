"""
`src/core.py`, `src/api.py`, and `src/utils.py` do `import sublime` / `import sublime_plugin` at module level, and use
a few of their names (as base classes, or in module-level constants) at *import* time. Those modules only exist inside
Sublime Text's embedded Python. To unit test the parts of this plugin that don't actually need a running Sublime
instance (parsing, querying, tree-walking), we install minimal stand-ins before anything under `src` is imported.

Nothing here needs to be a faithful `sublime` reimplementation: it only has to satisfy what's touched at import time
(base classes for commands/listeners, and the `KindId` values used to build `CAPTURE_NAME_TO_KIND`). Tests that need
`sublime`-ish runtime behavior (e.g. `Settings.to_dict()`, a fake `View`) build their own small fakes.
"""

from __future__ import annotations

import sys
import types

import pytest

# Kept small on purpose: instantiating a language downloads and caches its parser from `tree_sitter_language_pack`
# over the network the first time, so tests only pull in what they actually exercise.
LANGUAGES = ["python", "javascript", "markdown"]


def _install_sublime_stubs():
    if "sublime" in sys.modules:
        return

    sublime = types.ModuleType("sublime")
    sublime_plugin = types.ModuleType("sublime_plugin")

    class View: ...

    class Region:
        """Minimal stand-in: just enough for `begin`/`end`/`len`/construction, as used by `api.py`."""

        def __init__(self, a, b=None):
            self.a = a
            self.b = a if b is None else b

        def begin(self):
            return min(self.a, self.b)

        def end(self):
            return max(self.a, self.b)

        def __len__(self):
            return abs(self.b - self.a)

        def __eq__(self, other):
            return isinstance(other, Region) and (self.a, self.b) == (other.a, other.b)

    class KindId:
        AMBIGUOUS = "ambiguous"
        COLOR_DARK = "color_dark"
        COLOR_ORANGISH = "color_orangish"
        FUNCTION = "function"
        TYPE = "type"
        VARIABLE = "variable"

    sublime.View = View  # type: ignore[attr-defined]
    sublime.Region = Region  # type: ignore[attr-defined]
    sublime.KindId = KindId  # type: ignore[attr-defined]
    sublime.Kind = tuple  # type: ignore[attr-defined]  # only ever used as a type annotation
    sublime.active_window = lambda: None  # type: ignore[attr-defined]  # tests override this when they need a view

    for name in ("ApplicationCommand", "EventListener", "TextCommand", "WindowCommand", "TextChangeListener"):
        setattr(sublime_plugin, name, type(name, (), {}))

    sys.modules["sublime"] = sublime
    sys.modules["sublime_plugin"] = sublime_plugin


_install_sublime_stubs()


@pytest.fixture(scope="session", autouse=True)
def _instantiate_languages():
    """
    Populate `core.SCOPE_TO_LANGUAGE` for `LANGUAGES` once per test session, the same way `on_load` does, minus the
    real `sublime.Settings` object.
    """
    import sublime

    from src import core

    class FakeSettings:
        def to_dict(self):
            return {"installed_languages": LANGUAGES}

    sublime.load_settings = lambda name: FakeSettings()  # type: ignore[attr-defined]
    sublime.status_message = lambda *a, **k: None  # type: ignore[attr-defined]

    core.mutable_settings.d = {"installed_languages": LANGUAGES}
    core.instantiate_languages()
