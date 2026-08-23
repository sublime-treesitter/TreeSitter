# Sublime TreeSitter

The `TreeSitter` plugin provides Sublime Text with a performant and flexible interface to [Tree-sitter](https://tree-sitter.github.io/tree-sitter/).

## Why Tree-sitter

Tree-sitter builds a parse tree for text in any buffer, fast enough to update the tree after every keystroke. The `TreeSitter` plugin has built-in commands for syntax-based selection and navigation, and for managing and debugging Tree-sitter languages and parse trees.

It also has APIs with everything you need to build Sublime Text plugins for "structural" editing, selection, navigation, code folding, symbol maps… See e.g. https://zed.dev/blog/syntax-aware-editing for ideas.

## Installation

- Install `TreeSitter` from Package Control
- `TreeSitter` depends on [`tree_sitter`](https://github.com/tree-sitter/py-tree-sitter) and [`tree_sitter_language_pack`](https://github.com/xberg-io/tree-sitter-language-pack), which aren't installed as Package Control ["dependencies"](https://packagecontrol.io/docs/dependencies) yet (that requires a PR against [the Package Control channel](https://github.com/packagecontrol/channel)). Until then, install them yourself:
    - Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
    - Run `uv sync` from this plugin's directory (`Preferences: Browse Packages`, then `TreeSitter`)
    - Run `TreeSitter: Reload Plugin` from the command palette
    - If `tree_sitter_language_pack` still isn't importable, `TreeSitter` logs a status bar message telling you to redo this step

### Installed languages

The `installed_languages` setting controls which Sublime scopes `TreeSitter` actively parses and tracks. This doesn't control which languages are available; `tree_sitter_language_pack` can fetch and cache the parser for any of its languages on demand, whether or not it's in this list. Languages injected into another language's syntax tree are always resolved on demand regardless of this setting. 

Run `TreeSitter: Install Language` / `TreeSitter: Remove Language` to manage this list, and see more in [Languages](#languages) below.

## Overview

Sublime `TreeSitter` provides commands to:

- Select ancestor, descendant, sibling, or "cousin" nodes based on the current selection
- Goto symbols [returned by tree queries](./queries), with symbol breadcrumbs for context
- Print the syntax tree or nodes under the current selection (e.g. for debugging)

And APIs to:

- Get a node from a point or selection
- Get a Tree-sitter `Tree` by its buffer id, or get trees for all tracked buffers
- Subscribe to tree changes in any buffer in real time using `sublime_plugin.EventListener`
- Get a tree from a string of code
- Query a tree, walk a tree
- Other low-level APIs that power built-in commands

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

### Key bindings

Here are some [example key bindings](https://github.com/kylebebak/sublime_text_config/blob/aa2af3aadef035318009299504c161ba6d125f16/Default%20(OSX).sublime-keymap#L384-L577) for selection and navigation commands.

### Public APIs

`TreeSitter` exports [low-level APIs](./src/lib/sublime_tree_sitter/__init__.py) for building Sublime Text plugins. These APIs are importable by other plugins under the `sublime_tree_sitter` package.

API source code is mostly in [`src/api.py`](./src/api.py).

### Plugin load order

To import `sublime_tree_sitter` in your plugin, you have 2 options:

- Name your plugin so it comes after `TreeSitter` in alphabetical order (all `User` plugins do this)
- Import `sublime_tree_sitter` at "run time" after plugins have loaded, e.g. do something like this:

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

### Scopes, languages, and queries files

- A Sublime Text scope maps to a Tree-sitter language parser
    - Different scopes with the same syntax use the same language parser
    - E.g. `source.ts` and `source.ts.unittest` both use the `typescript` language parser
- A scope also maps to a queries file
    - Different scopes with the same syntax can map to different queries files
    - This way the plugin can index different symbols in e.g. `.ts` and `.test.ts` files
- If this plugin doesn't ship (and you haven't supplied) a `symbols.scm` for a language, we fall back to the "tags" query `tree_sitter_language_pack` bundles for it, then to Sublime's built-in goto

### Languages

`TreeSitter` gets language parsers from [`tree_sitter_language_pack`](https://github.com/xberg-io/tree-sitter-language-pack), which bundles ~370 languages. It downloads and caches the precompiled parser for a language the first time it's used (this can take a moment and needs network access), then reuses the cached copy from then on.

`SCOPE_TO_LANGUAGE_NAME` in [`src/utils.py`](./src/utils.py) only maps a curated subset of these languages to Sublime scopes out of the box. To add a scope for a language `tree_sitter_language_pack` supports but this plugin doesn't map yet, add it to `scope_to_language_name` in `TreeSitter: Settings`. Your mapping is merged with the default mapping.

#### Nested languages (injections)

Some languages embed others, e.g. Python in a Markdown fenced code block, JS/CSS in an HTML `<script>`/`<style>` tag, or Markdown's own inline content (implemented as two languages, `markdown` and `markdown_inline`, joined by an injection). `TreeSitter` discovers and parses these using each grammar's own bundled `injections.scm` via `tree_sitter_language_pack.get_injections_query`. This is the same mechanism editors like Neovim and Helix use. See `core.compute_injections`.

`get_node_spanning_region` and everything built on it (select ancestor/sibling/descendant, show node under selection) descends into injected trees. `get_cousins` doesn't, because "same depth, same type" only means something within one grammar. Goto/select symbol also search every injected tree (see `get_captures_from_nodes`), resolving a query per injected language the same way as the buffer's own top-level language. A symbol found inside an injected tree only gets breadcrumbs from within that tree, never stitched to the outer document's structure.

## Limitations

- Only supports source code encoded with ASCII / UTF-8 (Tree-sitter also supports UTF-16)

## Development

- Run `uv sync`
- Run tests with `uv run pytest`. These test parsing, querying, and tree-walking directly (with `tree_sitter` and `tree_sitter_language_pack`, no Sublime instance needed)

## License

[MIT](https://opensource.org/licenses/MIT).
