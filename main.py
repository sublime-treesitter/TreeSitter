from __future__ import annotations

import subprocess

import sublime  # noqa: F401
import sublime_plugin

from .src.utils import BUILD_PATH, DEPS_PATH, REPO_ROOT, add_path

SETTINGS_FILENAME = "TreeSitter.sublime-settings"
PYTHON_PATH = "/Users/kyle/.pyenv/versions/3.8.13/bin/python"
# If PIP_PATH isn't set, infer it from PYTHON_PATH
PIP_PATH = "/Users/kyle/.pyenv/versions/3.8.13/bin/pip"

#
# Code for installing tree sitter, and installing/building languages
#


def install_tree_sitter(pip_path: str = PIP_PATH):
    subprocess.run([pip_path, "install", "tree_sitter"], check=True)
    subprocess.run([pip_path, "install", "--target", str(DEPS_PATH), "tree_sitter"], check=True)


if False:
    # TODO: speed up pip install check
    install_tree_sitter()

add_path(str(DEPS_PATH))
import tree_sitter  # noqa


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


class TreeSitter(sublime_plugin.WindowCommand):
    def run(self):
        pass


class TreeSitterListener(sublime_plugin.TextChangeListener):
    def on_text_changed_async(self, changes: list[sublime.TextChange]):
        """
        Under the hood, ST puts TextChange instances onto a queue and handles them in FIFO order, regardless of how long
        on_text_changed_async takes to return. I tested this with `time.sleep` in `on_text_changed_async`.

        We need to know in which buffer text changes occur.

        All methods in TextChangeListener are tied to a buffer, but for some reason TextChange doesn't have a buffer
        attribute. `TextChangeListener.buffer` exists. This may be vulnerable to races, but testing with `time.sleep`
        suggests it's not. Will ask in Discord.

        LRU cache, dict of `(buffer_id, syntax)` tuple keys pointing to dict with tree instance and other metadata.

        When a buffer is attached, or reverted, or reloaded, we do an initial parse to get its tree, and cache that.

        When a text change occurs, we get its buffer (from the listener instance), get the buffer's primary view, get
        its syntax, look up the tree and metadata, and update/create the tree as necessary.

        We expose a method to get trees by their view, or by their buffer, and we returned cached tree or parse on
        demand if necessary.

        We also expose some convenience methods to walk a tree, etc.
        """

        if False:
            view = self.buffer.primary_view()
            change = changes[0]
            print("change", self.buffer.buffer_id, change.a.pt, view.syntax())

    def attach(self, buffer: sublime.Buffer):
        super().attach(buffer)

        if False:
            print("attach", self.buffer.buffer_id, buffer.primary_view().syntax())
