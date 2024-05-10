from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict, TypeVar, cast

import sublime

if TYPE_CHECKING:
    from typing_extensions import NotRequired

PROJECT_ROOT = Path(__file__).parent.parent
BUILD_PATH = Path(sublime.cache_path()) / "TreeSitter"
BUILD_PY_PATH = PROJECT_ROOT / "src" / "build.py"
QUERIES_PATH = PROJECT_ROOT / "queries"
LIB_PATH = PROJECT_ROOT / "src" / "lib"

SETTINGS_FILENAME = "TreeSitter.sublime-settings"

T = TypeVar("T")


def maybe_none(var: T) -> T | None:
    return var


def not_none(var: T | None) -> T:
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
    python_path: NotRequired[str]
    pip_path: NotRequired[str]
    language_name_to_scopes: NotRequired[dict[str, list[ScopeType]]]
    language_name_to_repo: NotRequired[dict[str, RepoDict]]
    language_name_to_parser_path: NotRequired[dict[str, str]]
    language_name_to_debounce_ms: NotRequired[dict[str, float]]
    debug: NotRequired[bool]
    queries_path: NotRequired[str]


ScopeType = Literal[
    "source.python",
    "source.ts",
    "source.tsx",
    "source.js",
    "source.jsx",
    "source.css",
    "source.scss",
    "source.go",
    "source.rust",
    "source.lua",
    "source.ruby",
    "source.java",
    "source.php",
    "source.zig",
    "source.c",
    "source.c++",
    "source.cs",
    "source.scala",
    "source.toml",
    "source.yaml",
    "source.json",
    "source.shell",
    "source.Kotlin",
    "source.julia",
    "source.haskell",
    "source.clojure",
    "source.elixir",
    "source.sql",
    "source.scheme",
    "text.html.vue",
    "text.html.svelte",
    "text.html.basic",
    "text.html.markdown",
    "text.xml",
    "source.erlang",
    "source.makefile",
    "source.dockerfile",
    "source.elm",
    "source.perl",
    "source.objc",
    "source.r",
    "text.restructuredtext",
    "source.ocaml",
    "source.regexp",
    "text.tex.latex",
    "source.hcl",
    "source.terraform",
    "source.hack",
]

LANGUAGE_NAME_TO_SCOPES: dict[str, list[ScopeType]] = {
    "python": ["source.python"],
    "typescript": ["source.ts"],
    "tsx": ["source.tsx"],
    "javascript": [
        "source.js",
        "source.jsx",
    ],
    "css": ["source.css"],
    "scss": ["source.scss"],
    "go": ["source.go"],
    "rust": ["source.rust"],
    "lua": ["source.lua"],
    "ruby": ["source.ruby"],
    "java": ["source.java"],
    "php": ["source.php"],
    "zig": ["source.zig"],
    "c": ["source.c"],
    "cpp": ["source.c++"],
    "c_sharp": ["source.cs"],
    "scala": ["source.scala"],
    "kotlin": ["source.Kotlin"],
    "julia": ["source.julia"],
    "haskell": ["source.haskell"],
    "clojure": ["source.clojure"],
    "elixir": ["source.elixir"],
    "toml": ["source.toml"],
    "yaml": ["source.yaml"],
    "json": ["source.json"],
    "bash": ["source.shell"],
    "query": ["source.scheme"],
    "vue": ["text.html.vue"],
    "svelte": ["text.html.svelte"],
    "sql": ["source.sql"],
    "html": [
        "text.html.basic",
        "text.xml",
    ],
    "markdown": ["text.html.markdown"],
    "erlang": ["source.erlang"],
    "make": ["source.makefile"],
    "dockerfile": ["source.dockerfile"],
    "elm": ["source.elm"],
    "perl": ["source.perl"],
    "objc": ["source.objc"],
    "r": ["source.r"],
    "rst": ["text.restructuredtext"],
    "ocaml": ["source.ocaml"],
    "regex": ["source.regexp"],
    "latex": ["text.tex.latex"],
    "hcl": ["source.hcl"],
    "terraform": ["source.terraform"],
    "hack": ["source.hack"],
}

"""
Notes on languages

- "markdown": "MDeiml/tree-sitter-markdown"
    - Not enabling this because it frequently crashes Sublime Text on edit, apparently also causes issues in neovim
    - https://github.com/MDeiml/tree-sitter-markdown/issues/114
"""


class RepoDict(TypedDict):
    """
    `branch` can be branch, tag, commit hash, anything that can be passed to `git checkout <>`

    This is e.g. for repos where `parser.c` isn't checked into main branch, or if user wants to peg to a git hash.
    """

    repo: str
    branch: NotRequired[str]
    parser_path: NotRequired[str]


LANGUAGE_NAME_TO_REPO: dict[str, RepoDict] = {
    "python": {"repo": "tree-sitter/tree-sitter-python"},
    "typescript": {"repo": "tree-sitter/tree-sitter-typescript", "parser_path": "typescript"},
    "tsx": {"repo": "tree-sitter/tree-sitter-typescript", "parser_path": "tsx"},
    "javascript": {"repo": "tree-sitter/tree-sitter-javascript"},
    "css": {"repo": "tree-sitter/tree-sitter-css"},
    "scss": {"repo": "serenadeai/tree-sitter-scss"},
    "go": {"repo": "tree-sitter/tree-sitter-go"},
    "rust": {"repo": "tree-sitter/tree-sitter-rust"},
    "lua": {"repo": "MunifTanjim/tree-sitter-lua"},
    "ruby": {"repo": "tree-sitter/tree-sitter-ruby"},
    "java": {"repo": "tree-sitter/tree-sitter-java"},
    "php": {"repo": "tree-sitter/tree-sitter-php"},
    "zig": {"repo": "maxxnino/tree-sitter-zig"},
    "c": {"repo": "tree-sitter/tree-sitter-c"},
    "cpp": {"repo": "tree-sitter/tree-sitter-cpp"},
    "c_sharp": {"repo": "tree-sitter/tree-sitter-c-sharp"},
    "scala": {"repo": "tree-sitter/tree-sitter-scala"},
    "toml": {"repo": "ikatyang/tree-sitter-toml"},
    "yaml": {"repo": "ikatyang/tree-sitter-yaml"},
    "json": {"repo": "tree-sitter/tree-sitter-json"},
    "bash": {"repo": "tree-sitter/tree-sitter-bash"},
    "vue": {"repo": "ikatyang/tree-sitter-vue"},
    "svelte": {"repo": "Himujjal/tree-sitter-svelte"},
    "html": {"repo": "tree-sitter/tree-sitter-html"},
    "markdown": {"repo": "ikatyang/tree-sitter-markdown"},
    "kotlin": {"repo": "fwcd/tree-sitter-kotlin"},
    "julia": {"repo": "tree-sitter/tree-sitter-julia"},
    "haskell": {"repo": "tree-sitter/tree-sitter-haskell"},
    "clojure": {"repo": "sogaiu/tree-sitter-clojure"},
    "elixir": {"repo": "elixir-lang/tree-sitter-elixir"},
    "query": {"repo": "nvim-treesitter/tree-sitter-query"},
    "sql": {"repo": "DerekStride/tree-sitter-sql", "branch": "gh-pages"},
    "ocaml": {"repo": "tree-sitter/tree-sitter-ocaml", "parser_path": "ocaml"},
    "elm": {"repo": "elm-tooling/tree-sitter-elm"},
    "r": {"repo": "r-lib/tree-sitter-r"},
    "dockerfile": {"repo": "camdencheek/tree-sitter-dockerfile"},
    "erlang": {"repo": "WhatsApp/tree-sitter-erlang"},
    "objc": {"repo": "jiyee/tree-sitter-objc"},
    "perl": {"repo": "ganezdragon/tree-sitter-perl"},
    "regex": {"repo": "tree-sitter/tree-sitter-regex"},
    "make": {"repo": "alemuller/tree-sitter-make"},
    "rst": {"repo": "stsewd/tree-sitter-rst"},
    "latex": {"repo": "latex-lsp/tree-sitter-latex"},
    "hcl": {"repo": "MichaHoffmann/tree-sitter-hcl"},
    "terraform": {"repo": "MichaHoffmann/tree-sitter-hcl", "parser_path": "dialects/terraform"},
    "hack": {"repo": "slackhq/tree-sitter-hack"},
}


def get_settings():
    """
    Note that during plugin startup, plugins can't call most `sublime` methods, including `load_settings`.

    [See more here](https://www.sublimetext.com/docs/api_reference.html#plugin-lifecycle).
    """
    return sublime.load_settings(SETTINGS_FILENAME)


def get_debug():
    return get_settings_dict().get("debug") or False


def get_settings_dict(settings: sublime.Settings | None = None):
    return cast(SettingsDict, (settings or get_settings()).to_dict())


def get_language_name_to_scopes():
    settings_d = get_settings_dict().get("language_name_to_scopes") or {}
    return {**LANGUAGE_NAME_TO_SCOPES, **settings_d}


def get_language_name_to_debounce_ms():
    return get_settings_dict().get("language_name_to_debounce_ms") or {}


def get_scope_to_language_name():
    scope_to_language_name: dict[ScopeType, str] = {}

    language_name_to_scopes = get_language_name_to_scopes()
    for language_name, scopes in language_name_to_scopes.items():
        for scope in scopes:
            scope_to_language_name[scope] = language_name
    return scope_to_language_name


def get_language_name_to_repo():
    settings_d = get_settings_dict().get("language_name_to_repo") or {}
    return {**LANGUAGE_NAME_TO_REPO, **settings_d}


def get_language_name_to_parser_path():
    language_name_to_parser_path: dict[str, str] = {}
    language_name_to_repo = get_language_name_to_repo()

    for name, repo_dict in language_name_to_repo.items():
        _, repo = repo_dict["repo"].split("/")
        if parser_path := repo_dict.get("parser_path"):
            language_name_to_parser_path[name] = str(Path(repo) / Path(parser_path))
        else:
            language_name_to_parser_path[name] = repo
    return language_name_to_parser_path


def get_queries_path():
    return get_settings_dict().get("queries_path") or str(QUERIES_PATH)
