# Sublime Tree-sitter

The `TreeSitter` plugin provides Sublime Text plugins with a performant and flexible interface to [Tree-sitter](https://tree-sitter.github.io/tree-sitter/).

## Overview

`TreeSitter` does the following:

- Installs [Tree-sitter Python bindings](https://github.com/tree-sitter/py-tree-sitter)
    - Importable by other plugins with `import tree_sitter`
- Installs and builds Tree-sitter languages, e.g. https://github.com/tree-sitter/tree-sitter-python
- Provides APIs for:
    - Getting a Tree-sitter `Tree` by its buffer id, getting trees for all tracked buffers
    - Subscribing to tree changes in any buffer in real time using `sublime_plugin.EventListener`
    - Getting a tree from a string of code
    - Querying a tree, walking a tree

## Installation

- Install `TreeSitter` from Package Control
- Go to `TreeSitter: Settings`, and set `python_path` to point to a Python 3.8 executable on your machine

## Usage

```py
from sublime_tree_sitter import get_tree_dict, query_tree, walk_tree
```

[See more here](./src/lib/sublime_tree_sitter/__init__.py).

### Event listener

## Limitations

- Doesn't support nested syntax trees, e.g. JS code in `<script>` tags in HTML docs
- Due to limitations in Sublime's bundled Python, requires an external Python 3.8 executable

## License

Licensed under the [MIT License](https://opensource.org/licenses/MIT).
