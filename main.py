import subprocess

import sublime  # noqa: F401
import sublime_plugin

from .src.utils import BUILD_PATH, DEPS_PATH, REPO_ROOT, add_path

PYTHON_PATH = "/Users/kyle/.pyenv/versions/3.8.13/bin/python"
PIP_PATH = "/Users/kyle/.pyenv/versions/3.8.13/bin/pip"


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

SETTINGS_FILENAME = "TreeSitter.sublime-settings"


class TreeSitter(sublime_plugin.WindowCommand):
    def run(self):
        pass
