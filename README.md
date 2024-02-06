# Sublime TreeSitter

The `TreeSitter` plugin provides Sublime Text with a performant and flexible interface to [Tree-sitter](https://tree-sitter.github.io/tree-sitter/).

## Why Tree-sitter

Tree-sitter builds a parse tree for text in any buffer, fast enough to update the tree after every keystroke. The `TreeSitter` plugin has built-in commands for managing and debugging Tree-sitter languages and parse trees, and for syntax-based selection and navigation.

It also has APIs with everything you need to build Sublime Text plugins for "structural" editing, selection, navigation, code folding, symbol maps… See e.g. https://zed.dev/blog/syntax-aware-editing for ideas.

## Overview

Sublime `TreeSitter` provides commands to:

- Select ancestor, descendant, sibling, or "cousin" nodes based on the current selection
- Goto symbols [returned by tree queries](./queries)
- Print the syntax tree or nodes under the current selection (e.g. for debugging)

And APIs to:

- Get a node from a point or selection
- Get a Tree-sitter `Tree` by its buffer id, or get trees for all tracked buffers
- Subscribe to tree changes in any buffer in real time using `sublime_plugin.EventListener`
- Get a tree from a string of code
- Query a tree, walk a tree
- Other low-level APIs that power built-in commands

## Installation

- Install `TreeSitter` from Package Control
- See installed languages / install a new language with `TreeSitter: Install Language`
    - `python`, `json`, `javascript`, `typescript` and a few others are installed by default

## Usage

Here's a partial list of commands that ship with `TreeSitter`. To see them all, search for `TreeSitter` in the command palette.

- `tree_sitter_install_language`
- `tree_sitter_remove_language`
- `tree_sitter_select_ancestor`
- `tree_sitter_select_sibling`
- `tree_sitter_select_cousins`
- `tree_sitter_select_descendant`
- `tree_sitter_select_symbols`
- `tree_sitter_goto_symbol`
- `tree_sitter_print_tree`
- `tree_sitter_show_node_under_selection`

And here are some [example key bindings](https://github.com/kylebebak/sublime_text_config/blob/aa2af3aadef035318009299504c161ba6d125f16/Default%20(OSX).sublime-keymap#L384-L577) for selection and navigation commands.

### Public APIs

`TreeSitter` exports [low-level APIs](./src/lib/sublime_tree_sitter/__init__.py) for building Sublime Text plugins. These APIs are importable by other plugins under the `sublime_tree_sitter` package.

API source code is mostly in [`src/api.py`](./src/api.py).

### Plugin load order

To import `sublime_tree_sitter` in your plugin, you have 2 options:

- Name your plugin so it comes after `TreeSitter` in alphabetical order (all `User` plugins do this)
- Or, import `sublime_tree_sitter` after your plugin has loaded, e.g. do something like this:

```py
import sublime_plugin


class MyTreeSitterCommand(sublime_plugin.WindowCommand):
    def run(self, **kwargs):
        from sublime_tree_sitter import get_tree_dict
        # ...
```

### Event listener

Plugins can subscribe to `"tree_sitter_update_tree"` events:

```py
import sublime_plugin
from sublime_tree_sitter import get_tree_dict


class MyTreeSitterListener(sublime_plugin.EventListener):
    def on_window_command(self, window, command, args):
        if command == "tree_sitter_update_tree":
            print(get_tree_dict(args["buffer_id"]))
```

### Manage your own language repos and binaries

`TreeSitter` ships with pre-built language binaries from [the `tree_sitter_languages` package](https://github.com/grantjenks/py-tree-sitter-languages). If you want to use languages or language versions not in this package, `TreeSitter` can clone language repos and build binaries for you.

To enable this (and disable languages bundled in `tree_sitter_languages`), go to `TreeSitter: Settings` from the command palette, and set `python_path` to an external Python 3.8 executable with a working C compiler, so it can call [`Language.build_library`](https://github.com/tree-sitter/py-tree-sitter/blob/565f1654d1849e966c77326e11e65ba6ef530feb/tree_sitter/__init__.py#L63).

If you use Linux or MacOS, an easy way to get Python 3.8 [is with pyenv](https://github.com/pyenv/pyenv).

## Limitations

- Doesn't support nested syntax trees, e.g. JS code in `<script>` tags in HTML docs
- Only supports source code encoded with ASCII / UTF-8 (Tree-sitter also supports UTF-16)

## License

[MIT](https://opensource.org/licenses/MIT).
