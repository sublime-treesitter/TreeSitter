from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Literal, TypeVar

import sublime

T = TypeVar("T")

PROJECT_ROOT = Path(__file__).parent.parent
DEPS_PATH = PROJECT_ROOT / "deps"
BUILD_PATH = PROJECT_ROOT / "build"
LIB_PATH = PROJECT_ROOT / "src" / "lib"


def log(s: str, with_print = True, with_status = False):
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


def not_none(var: T | None) -> T:
    """
    This narrows type from `T | None` -> `T`.
    """
    assert var is not None
    return var


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
    "text.html.vue",
    "text.html.svelte",
    "text.html.basic",
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
    "toml": ["source.toml"],
    "yaml": ["source.yaml"],
    "json": [
        "source.json",
        "source.json.sublime",
        "source.json.sublime.keymap",
        "source.json.sublime.commands",
        "source.json.sublime.theme"
    ],
    "bash": ["source.shell.bash"],
    "vue": ["text.html.vue"],
    "svelte": ["text.html.svelte"],
    "html": ["text.html.basic"],
}

SCOPE_TO_LANGUAGE_NAME: dict[ScopeType, str] = {}
for language_name, scopes in LANGUAGE_NAME_TO_SCOPES.items():
    for scope in scopes:
        SCOPE_TO_LANGUAGE_NAME[scope] = language_name

LANGUAGE_NAME_TO_REPO = {
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
}

"""
Notes on languages

- "markdown": "MDeiml/tree-sitter-markdown"
    - Not enabling this because it frequently crashes Sublime Text on edit, apparently also causes issues in neovim
    - https://github.com/MDeiml/tree-sitter-markdown/issues/114
"""

LANGUAGE_NAME_TO_PATH: dict[str, str] = {}
for name, org_and_repo in LANGUAGE_NAME_TO_REPO.items():
    _, repo = org_and_repo.split("/")
    LANGUAGE_NAME_TO_PATH[name] = repo

# Overrides for special repos in which parser.c isn't at src/parser.c
LANGUAGE_NAME_TO_PATH["typescript"] = str(Path("tree-sitter-typescript") / "typescript")
LANGUAGE_NAME_TO_PATH["tsx"] = str(Path("tree-sitter-typescript") / "tsx")
