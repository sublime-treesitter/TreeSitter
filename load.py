from .src.api import (  # noqa: F401
    TreeSitterGotoSymbolCommand,
    TreeSitterOnSelectionModifiedListener,
    TreeSitterPrintTreeCommand,
    TreeSitterSelectAncestorCommand,
    TreeSitterSelectCousinsCommand,
    TreeSitterSelectDescendantCommand,
    TreeSitterSelectSiblingCommand,
    TreeSitterSelectSymbolsCommand,
    TreeSitterShowNodeUnderSelectionCommand,
    TreeSitterToggleShowNodeUnderSelectionCommand,
)
from .src.core import (  # noqa: F401
    TreeSitterEventListener,
    TreeSitterInstallLanguageCommand,
    TreeSitterRemoveLanguageCommand,
    TreeSitterTextChangeListener,
    TreeSitterUpdateLanguageCommand,
    TreeSitterUpdateTreeCommand,
    on_load,
)


def plugin_loaded():
    """
    See docstring for `on_load`.
    """
    on_load()
