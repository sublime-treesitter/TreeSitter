from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Literal, TypedDict, cast

import sublime

PROJECT_ROOT = Path(__file__).parent.parent
DEPS_PATH = PROJECT_ROOT / "deps"
BUILD_PATH = PROJECT_ROOT / "build"
LIB_PATH = PROJECT_ROOT / "src" / "lib"

SETTINGS_FILENAME = "TreeSitter.sublime-settings"


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
    installed_languages: List[str]
    python_path: str
    pip_path: str
    language_name_to_scopes: Dict[str, List[ScopeType]] | None
    language_name_to_org_and_repo: Dict[str, str] | None
    language_name_to_parser_path: Dict[str, str] | None
    debug: bool | None


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
    "source.json.sublime",
    "source.json.sublime.keymap",
    "source.json.sublime.commands",
    "source.json.sublime.theme",
    "source.shell.bash",
    "source.Kotlin",
    "source.julia",
    "source.haskell",
    "source.clojure",
    "source.elixir",
    "text.html.vue",
    "text.html.svelte",
    "text.html.basic",
    "text.html.markdown",
]

LANGUAGE_NAME_TO_SCOPES: Dict[str, List[ScopeType]] = {
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
    "json": [
        "source.json",
        "source.json.sublime",
        "source.json.sublime.keymap",
        "source.json.sublime.commands",
        "source.json.sublime.theme",
    ],
    "bash": ["source.shell.bash"],
    "vue": ["text.html.vue"],
    "svelte": ["text.html.svelte"],
    "html": ["text.html.basic"],
    "markdown": ["text.html.markdown"],
}

"""
Notes on languages

- "markdown": "MDeiml/tree-sitter-markdown"
    - Not enabling this because it frequently crashes Sublime Text on edit, apparently also causes issues in neovim
    - https://github.com/MDeiml/tree-sitter-markdown/issues/114
"""

LANGUAGE_NAME_TO_ORG_AND_REPO = {
    "python": "tree-sitter/tree-sitter-python",
    "typescript": "tree-sitter/tree-sitter-typescript",
    "tsx": "tree-sitter/tree-sitter-typescript",
    "javascript": "tree-sitter/tree-sitter-javascript",
    "css": "tree-sitter/tree-sitter-css",
    "scss": "serenadeai/tree-sitter-scss",
    "go": "tree-sitter/tree-sitter-go",
    "rust": "tree-sitter/tree-sitter-rust",
    "lua": "MunifTanjim/tree-sitter-lua",
    "ruby": "tree-sitter/tree-sitter-ruby",
    "java": "tree-sitter/tree-sitter-java",
    "php": "tree-sitter/tree-sitter-php",
    "zig": "maxxnino/tree-sitter-zig",
    "c": "tree-sitter/tree-sitter-c",
    "cpp": "tree-sitter/tree-sitter-cpp",
    "c_sharp": "tree-sitter/tree-sitter-c-sharp",
    "scala": "tree-sitter/tree-sitter-scala",
    "toml": "ikatyang/tree-sitter-toml",
    "yaml": "ikatyang/tree-sitter-yaml",
    "json": "tree-sitter/tree-sitter-json",
    "bash": "tree-sitter/tree-sitter-bash",
    "vue": "ikatyang/tree-sitter-vue",
    "svelte": "Himujjal/tree-sitter-svelte",
    "html": "tree-sitter/tree-sitter-html",
    "markdown": "ikatyang/tree-sitter-markdown",
    "kotlin": "fwcd/tree-sitter-kotlin",
    "julia": "tree-sitter/tree-sitter-julia",
    "haskell": "tree-sitter/tree-sitter-haskell",
    "clojure": "sogaiu/tree-sitter-clojure",
    "elixir": "elixir-lang/tree-sitter-elixir",
}

LANGUAGE_NAME_TO_PARSER_PATH: dict[str, str] = {}
for name, org_and_repo in LANGUAGE_NAME_TO_ORG_AND_REPO.items():
    _, repo = org_and_repo.split("/")
    LANGUAGE_NAME_TO_PARSER_PATH[name] = repo

# Overrides for special Tree-sitter grammar repos in which parser.c isn't at src/parser.c
LANGUAGE_NAME_TO_PARSER_PATH["typescript"] = str(Path("tree-sitter-typescript") / "typescript")
LANGUAGE_NAME_TO_PARSER_PATH["tsx"] = str(Path("tree-sitter-typescript") / "tsx")


def get_settings():
    """
    Note that during plugin startup, plugins can't call most `sublime` methods, including `load_settings`.

    [See more here](https://www.sublimetext.com/docs/api_reference.html#plugin-lifecycle).
    """
    return sublime.load_settings(SETTINGS_FILENAME)


def get_settings_dict():
    return cast(SettingsDict, get_settings().to_dict())


def get_language_name_to_scopes():
    settings_d = get_settings_dict().get("language_name_to_scopes") or {}
    return {**LANGUAGE_NAME_TO_SCOPES, **settings_d}


def get_scope_to_language_name():
    scope_to_language_name: dict[ScopeType, str] = {}

    language_name_to_scopes = get_language_name_to_scopes()
    for language_name, scopes in language_name_to_scopes.items():
        for scope in scopes:
            scope_to_language_name[scope] = language_name
    return scope_to_language_name


def get_language_name_to_org_and_repo():
    settings_d = get_settings_dict().get("language_name_to_org_and_repo") or {}
    return {**LANGUAGE_NAME_TO_ORG_AND_REPO, **settings_d}


def get_language_name_to_parser_path():
    settings_d = get_settings_dict().get("language_name_to_parser_path") or {}
    return {**LANGUAGE_NAME_TO_PARSER_PATH, **settings_d}
