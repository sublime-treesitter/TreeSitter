from .src.api import (  # noqa: F401
    TreeSitterGotoQueryCommand,
    TreeSitterPrintQueryCommand,
    TreeSitterPrintTreeCommand,
    TreeSitterSelectAncestorCommand,
    TreeSitterSelectCousinsCommand,
    TreeSitterSelectDescendantCommand,
    TreeSitterSelectQueryCommand,
    TreeSitterSelectSiblingCommand,
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
