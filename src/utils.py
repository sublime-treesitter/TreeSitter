from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal, Tuple, TypeVar, cast, get_args

T = TypeVar("T")


REPO_ROOT = Path(__file__).parent.parent
DEPS_PATH = REPO_ROOT / "deps"
BUILD_PATH = REPO_ROOT / "build"


def not_none(var: T | None) -> T:
    """
    This narrows type from `T | None` -> `T`.
    """
    assert var is not None
    return var


def add_path(path: str):
    """
    https://stackoverflow.com/a/1893663/5823904
    """
    if path not in sys.path:
        sys.path.insert(0, path)


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
    "source.json",  # Any scope that starts with `source.json.` uses JSON language, e.g. source.json.sublime.settings
    "source.shell.bash",
    "text.html.vue",
    "text.html.svelte",
    "text.html.basic",
    "text.html.markdown",
]
SCOPES = cast(Tuple[ScopeType, ...], get_args(ScopeType))

SCOPE_TO_LANGUAGE_NAME: dict[ScopeType, str] = {
    "source.python": "python",
    "source.ts": "typescript",
    "source.tsx": "typescript",
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
    "source.zig": "zig",
    "source.c": "c",
    "source.c++": "cpp",
    "source.cs": "c_sharp",
    "source.scala": "scala",
    "source.toml": "toml",
    "source.yaml": "yaml",
    "source.json": "json",
    "source.shell.bash": "bash",
    "text.html.vue": "vue",
    "text.html.svelte": "svelte",
    "text.html.basic": "basic",
    "text.html.markdown": "markdown",
}

LANGUAGE_NAME_TO_REPO = {
    "python": "tree-sitter/tree-sitter-python",
    "typescript": "tree-sitter/tree-sitter-typescript",
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
    "markdown": "MDeiml/tree-sitter-markdown",
}
