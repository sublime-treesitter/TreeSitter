import sys
prefix = __package__ + '.'  # don't clear the base package
for module_name in [
        module_name for module_name in sys.modules
        if module_name.startswith(prefix) and module_name != __name__]:
    del sys.modules[module_name]
del prefix

from .src.api import (  # noqa: F401
    TreeSitterReloadCommand,
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
