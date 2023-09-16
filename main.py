from __future__ import annotations

import subprocess
from typing import Literal, Tuple, TypedDict, cast, get_args

import sublime  # noqa: F401
import sublime_plugin
from sublime import View

from .src.utils import BUILD_PATH, DEPS_PATH, REPO_ROOT, add_path

SETTINGS_FILENAME = "TreeSitter.sublime-settings"
PYTHON_PATH = "/Users/kyle/.pyenv/versions/3.8.13/bin/python"
# If PIP_PATH isn't set, infer it from PYTHON_PATH
PIP_PATH = "/Users/kyle/.pyenv/versions/3.8.13/bin/pip"

#
# Code for installing tree sitter, and installing/building languages
#

# Note: `installed_languages` specified in settings, Python and a few others installed by default


def install_tree_sitter(pip_path: str = PIP_PATH):
    subprocess.run([pip_path, "install", "--target", str(DEPS_PATH), "tree_sitter"], check=True)


if False:
    # TODO: speed up pip install check
    install_tree_sitter()

add_path(str(DEPS_PATH))
from tree_sitter import Language, Parser, Tree  # noqa


def clone_language(language_repo: str):
    subprocess.run(
        ["git", "clone", f"https://github.com/tree-sitter/{language_repo}", str(BUILD_PATH / language_repo)],
        check=False,
    )


def build_languages():
    # TODO: don't build unless necessary
    subprocess.run([PYTHON_PATH, str(REPO_ROOT / "src" / "build.py")], check=True)


if False:
    clone_language("tree-sitter-python")
    build_languages()

#
# Code for caching syntax trees by their `buffer_id`s, and keeping them in sync as `TextChange`s occur
# https://www.sublimetext.com/docs/api_reference.html#sublime.View
#

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
    "source.swift",
    "source.scala",
    "source.sql",
    "source.toml",
    "source.yaml",
    "source.json",  # Any scope that starts with `source.json.` uses JSON language, e.g. source.json.sublime.settings
    "source.dockerfile",
    "source.shell.bash",
    "text.html.vue",
    "text.html.svelte",
    "text.html.markdown",
    "text.html.basic",
    "text.git.ignore",
    "text.tex.latex",
]
scopes = cast(Tuple[ScopeType, ...], get_args(ScopeType))


def check_scope(scope: str | None):
    if not scope or scope not in SCOPE_TO_LANGUAGE:
        return None
    return scope


def edit(parser: Parser, scope: ScopeType, change: sublime.TextChange, tree: Tree) -> Tree:
    parser.set_language(SCOPE_TO_LANGUAGE[scope])
    return tree


def parse(parser: Parser, scope: ScopeType, s: str) -> Tree:
    parser.set_language(SCOPE_TO_LANGUAGE[scope])
    return parser.parse(s.encode())


class TreeDict(TypedDict):
    tree: Tree


def get_scope(view: View) -> ScopeType | None:
    syntax = view.syntax()
    if not syntax:
        return None
    return cast(ScopeType, syntax.scope)


def publish_tree_update(window: sublime.Window | None, buffer_id: int, scope: str):
    if not window:
        return

    window.run_command(
        "tree_sitter_update_tree",
        {
            "buffer_id": buffer_id,
            "scope": scope or "",
        },
    )


class TreeSitterUpdateTreeCommand(sublime_plugin.WindowCommand):
    """
    So client code can "subscribe" to tree updates with an `EventListener`. For example:

    ```py
    class Listener(sublime_plugin.EventListener):
        def on_window_command(self, window, command, args):
            print(command, args["buffer_id"])
    ```
    """

    def run(self, **kwargs):
        pass


SCOPE_TO_LANGUAGE: dict[ScopeType, Language] = {}
BUFFER_ID_TO_TREE: dict[int, TreeDict] = {}


class TreeSitterEventListener(sublime_plugin.EventListener):
    """
    One of these for the whole Sublime instance. We use it for the `on_load_async` hook.
    """

    def __init__(self):
        super().__init__()
        self.parser = Parser()

    def handle_load(self, view: View):
        syntax = view.syntax()
        scope = syntax and syntax.scope
        if not (scope := check_scope(scope)):
            return

        buffer_id = view.buffer().id()
        tree = parse(self.parser, scope, s=view.substr(sublime.Region(0, view.size())))

        BUFFER_ID_TO_TREE[buffer_id] = {"tree": tree}

        publish_tree_update(view.window(), buffer_id=buffer_id, scope=scope)

    def on_load_async(self, view: View):
        self.handle_load(view)

    def on_reload_async(self, view: View):
        self.handle_load(view)

    def on_revert_async(self, view: View):
        self.handle_load(view)


class TreeSitterTextChangeListener(sublime_plugin.TextChangeListener):
    """
    One of these is instantiated per buffer.
    """

    def __init__(self):
        super().__init__()
        self.parser = Parser()

    def on_text_changed_async(self, changes: list[sublime.TextChange]):
        """
        Under the hood, ST synchronously puts any async callbacks onto a queue. It asynchronously handles them in FIFO
        order in a separate thread. Source code suggests this, testing with `time.sleep` confirms it. This ensures
        there are no races between "text change" events (almost always edit) and "load" (always parse).

        We need to know in which buffer text changes occur. All methods in TextChangeListener are tied to the buffer for
        which the listener was instantiated, so this is trivial.

        LRU cache, dict of `(buffer_id, syntax)` tuple keys pointing to dict with tree instance and other metadata.

        When a buffer is loaded, reverted, or reloaded, we do an initial parse to get its tree, and cache that.

        When a text change occurs, we get its buffer, get the buffer's primary view, get its syntax, look up the tree
        and metadata, and update/create the tree as necessary.

        We expose a method to get trees by their buffer, and we returned cached tree or parse on demand if necessary.

        We also expose some convenience methods to walk a tree, etc.
        """

        view = self.buffer.primary_view()
        syntax = view.syntax()
        scope = syntax and syntax.scope
        if not (scope := check_scope(scope)):
            return

        buffer_id = self.buffer.id()

        for change in changes:
            if buffer_id not in BUFFER_ID_TO_TREE:
                tree = parse(self.parser, scope, s=view.substr(sublime.Region(0, view.size())))
            else:
                tree = edit(self.parser, scope, change, BUFFER_ID_TO_TREE[buffer_id]["tree"])

            BUFFER_ID_TO_TREE[buffer_id] = {"tree": tree}

        # May as well handle all changes before "publishing" update
        publish_tree_update(view.window(), buffer_id=buffer_id, scope=scope)
