from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, TypeVar, cast

import sublime

if TYPE_CHECKING:
    from typing import NotRequired

PROJECT_ROOT = Path(__file__).parent.parent
QUERIES_PATH = PROJECT_ROOT / "queries"
LIB_PATH = PROJECT_ROOT / "src" / "lib"


def get_deps_path() -> Path:
    """
    For development only.

    Third-party dependencies (`tree_sitter`, `tree_sitter_language_pack`) are installed as Package Control dependencies,
    but, for development, they can also be installed using `uv sync` into this plugin's own .venv, and this path (the
    `.venv`'s `site-packages`) can be added to the Sublime plugin host's `sys.path` at load time with `add_path`.
    """
    venv = PROJECT_ROOT / ".venv"
    if os.name == "nt":
        return venv / "Lib" / "site-packages"
    return venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"


DEPS_PATH = get_deps_path()

SETTINGS_FILENAME = "TreeSitter.sublime-settings"

T = TypeVar("T")


def maybe_none[T](var: T) -> T | None:
    return var


def not_none[T](var: T | None) -> T:
    assert var is not None
    return var


def log(s: str, with_print=True, with_status=False):
    msg = f"Tree-sitter: {s}"
    if with_print:
        print(msg)
    if with_status:
        sublime.status_message(msg)


def add_path(path: str):
    """
    Add path to "Python path", i.e. `sys.path`. Idempotent.

    https://stackoverflow.com/a/1893663/5823904
    """
    if path not in sys.path:
        sys.path.insert(0, path)


class SettingsDict(TypedDict):
    installed_languages: list[str]
    scope_to_language_name: NotRequired[dict[ScopeType, str]]
    scope_to_queries_name: NotRequired[dict[ScopeType, str]]
    language_name_to_debounce_ms: NotRequired[dict[str, float]]
    debug: NotRequired[bool]
    queries_path: NotRequired[str]
    file_ignore_patterns: NotRequired[list[str]]


# A Sublime Text scope, e.g. `"source.python"` (a view's base scope) - just `str`, not a `Literal` of every scope this
# plugin happens to have a mapping for in `SCOPE_TO_LANGUAGE_NAME`: that mapping is meant to be extended freely (by
# this plugin and by users, via the `scope_to_language_name` setting), and a `Literal` would need editing every time
# it grows, for no real type-safety benefit (scopes always originate as plain strings from the Sublime API).
type ScopeType = str

SCOPE_TO_LANGUAGE_NAME: dict[ScopeType, str] = {
    "source.python": "python",
    "source.ts": "typescript",
    "source.ts.unittest": "typescript",
    "source.tsx": "tsx",
    "source.tsx.unittest": "tsx",
    "source.js": "javascript",
    "source.jsx": "javascript",
    "source.css": "css",
    "source.scss": "scss",
    "source.go": "go",
    "source.rust": "rust",
    "source.lua": "lua",
    "source.ruby": "ruby",
    "source.java": "java",
    "source.php": "php",
    "embedding.php": "php",
    "text.html.php": "php",
    "source.zig": "zig",
    "source.c": "c",
    "source.c++": "cpp",
    "source.cs": "csharp",
    "source.scala": "scala",
    "source.Kotlin": "kotlin",
    "source.julia": "julia",
    "source.haskell": "haskell",
    "source.clojure": "clojure",
    "source.elixir": "elixir",
    "source.toml": "toml",
    "source.yaml": "yaml",
    "source.json": "json",
    "source.jsonl": "json",
    "source.shell": "bash",
    "source.scheme": "query",
    "text.html.vue": "vue",
    "text.html.svelte": "svelte",
    "source.sql": "sql",
    "text.html.basic": "html",
    "text.xml": "xml",
    "text.html.markdown": "markdown",
    "source.erlang": "erlang",
    "source.makefile": "make",
    "source.dockerfile": "dockerfile",
    "source.elm": "elm",
    "source.perl": "perl",
    "source.objc": "objc",
    "source.r": "r",
    "text.restructuredtext": "rst",
    "source.ocaml": "ocaml",
    "source.regexp": "regex",
    "text.tex.latex": "latex",
    "source.hcl": "hcl",
    "source.terraform": "terraform",
    "source.hack": "hack",
    # Scopes below are for syntaxes that ship with Sublime Text out of the box (as opposed to the ones above this
    # comment; some of those are default scopes, and some are not). This isn't an exhaustive list of default syntaxes,
    # and `scope_to_language_name` in settings can always extend it, but it's worth keeping reasonably complete, since
    # these scopes are stable (unlike scopes in community packages, which are at the whim of their maintainers).
    "source.actionscript": "actionscript",
    "source.applescript": "applescript",
    "source.dosbatch": "batch",
    "source.d": "d",
    "source.diff": "diff",
    "source.diff.basic": "diff",
    "source.dot": "dot",
    "source.groovy": "groovy",
    "source.lisp": "commonlisp",
    "source.matlab": "matlab",
    "source.tcl": "tcl",
    "text.tex": "latex",
    "text.xml.dtd": "dtd",
    "text.bibtex": "bibtex",
    "text.git.attributes": "gitattributes",
    "text.git.commit": "gitcommit",
    "text.git.commit-message": "gitcommit",
    "text.git.config": "git_config",
    "text.git.ignore": "gitignore",
    "text.git.rebase": "git_rebase",
    "text.haml": "haml",
    "source.shell.bash": "bash",
    "source.shell.zsh": "zsh",
    "source.regexp.basic": "regex",
    "source.sql.basic": "sql",
    "source.clojure.clojurescript": "clojure",
    "source.java-props": "properties",
}

SCOPE_TO_QUERIES_NAME: dict[ScopeType, str] = {
    **SCOPE_TO_LANGUAGE_NAME,
    "source.ts.unittest": "typescript_test",
    "source.tsx.unittest": "tsx_test",
}


def get_settings():
    """
    Note that during plugin startup, plugins can't call most `sublime` methods, including `load_settings`.

    [See more here](https://www.sublimetext.com/docs/api_reference.html#plugin-lifecycle).
    """
    return sublime.load_settings(SETTINGS_FILENAME)


def get_settings_dict() -> SettingsDict:
    return cast(SettingsDict, get_settings().to_dict())


def get_debug(d: SettingsDict | None = None):
    d = d or get_settings_dict()
    return d.get("debug") or False


def get_file_ignore_patterns(d: SettingsDict | None = None):
    d = d or get_settings_dict()
    return d.get("file_ignore_patterns", [])


def get_scope_to_language_name(d: SettingsDict | None = None) -> dict[ScopeType, str]:
    d = d or get_settings_dict()
    return {**SCOPE_TO_LANGUAGE_NAME, **d.get("scope_to_language_name", {})}


def get_scope_to_queries_name(d: SettingsDict | None = None) -> dict[ScopeType, str]:
    d = d or get_settings_dict()
    return {**SCOPE_TO_QUERIES_NAME, **d.get("scope_to_queries_name", {})}


def get_language_name_to_scopes(d: SettingsDict | None = None):
    scope_to_language_name = get_scope_to_language_name(d)
    language_name_to_scopes: dict[str, list[ScopeType]] = {}
    for scope, language in scope_to_language_name.items():
        if language in language_name_to_scopes:
            language_name_to_scopes[language].append(scope)
        else:
            language_name_to_scopes[language] = [scope]
    return language_name_to_scopes


def get_language_name_to_debounce_ms(d: SettingsDict | None = None):
    d = d or get_settings_dict()
    return d.get("language_name_to_debounce_ms", {})


def get_queries_path(d: SettingsDict | None = None):
    d = d or get_settings_dict()
    return d.get("queries_path") or str(QUERIES_PATH)
