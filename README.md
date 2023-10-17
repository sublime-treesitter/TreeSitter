# Sublime TreeSitter

The `TreeSitter` plugin provides Sublime Text plugins with a performant and flexible interface to [Tree-sitter](https://tree-sitter.github.io/tree-sitter/).

## Why Tree-sitter

Tree-sitter builds a parse tree for text in any buffer, fast enough to update the tree after every keystroke. The `TreeSitter` plugin provides everything you need to build Sublime Text plugins for "structural" editing, selection, navigation, code folding, symbol maps… See e.g. https://zed.dev/blog/syntax-aware-editing for ideas.

## Overview

Sublime `TreeSitter` does the following:

- Installs [Tree-sitter Python bindings](https://github.com/tree-sitter/py-tree-sitter)
    - Importable by other plugins with `import tree_sitter`
- Installs and builds Tree-sitter languages, e.g. https://github.com/tree-sitter/tree-sitter-python
- Provides APIs for:
    - Getting a Tree-sitter `Tree` by its buffer id, getting trees for all tracked buffers
    - Subscribing to tree changes in any buffer in real time using `sublime_plugin.EventListener`
    - Getting a tree from a string of code
    - Querying a tree, walking a tree
    - Getting a node from a point or selection, getting a region from a node

## Installation

- Install `TreeSitter` from Package Control
- Go to `TreeSitter: Settings`, and set `python_path` to a Python 3.8 executable on your machine
- Install new languages with `TreeSitter: Install Language`
    - `python` and `json` installed by default

## Usage

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

### Public APIs

[See here](./src/lib/sublime_tree_sitter/__init__.py).

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

## Limitations

- Doesn't support nested syntax trees, e.g. JS code in `<script>` tags in HTML docs
- Due to limitations in Sublime's bundled Python, requires an external Python 3.8 executable

## License

Licensed under the [MIT License](https://opensource.org/licenses/MIT).
