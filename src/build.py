"""
This can be called with a system python (not Sublime's python) in a subprocess to build .so files.
"""
import sys
from pathlib import Path

# Some import issue prevents us from doing `from .utils import DEPS_PATH, add_path`
PROJECT_ROOT = Path(__file__).parent.parent
DEPS_PATH = str(PROJECT_ROOT / "deps")

if DEPS_PATH not in sys.path:
    sys.path.insert(0, DEPS_PATH)

from tree_sitter import Language  # noqa: E402

language_source_path = sys.argv[1]
language_file_path = sys.argv[2]

Language.build_library(
    # Create shared object / dynamically linked library at language_file_path
    language_file_path,
    # Include just one language per .so file, easier to manage
    [language_source_path],
)
