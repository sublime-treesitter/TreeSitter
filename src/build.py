"""
This can be called with a system python (not Sublime's python) in a subprocess to build .so files.
"""
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version

pip_path = sys.argv[1]
language_source_path = sys.argv[2]
language_file_path = sys.argv[3]

TREE_SITTER_BINDINGS_VERSION = "0.20.4"

try:
    v = version("tree_sitter")
except PackageNotFoundError:
    v = ""
if v != TREE_SITTER_BINDINGS_VERSION:
    # Bindings non installed/correct version not installed; call with `check=True` to block until subprocess completes
    subprocess.run([pip_path, "install", f"tree_sitter=={TREE_SITTER_BINDINGS_VERSION}"], check=True)

from tree_sitter import Language  # noqa: E402

Language.build_library(
    # Create shared object / dynamically linked library at language_file_path
    language_file_path,
    # Include just one language per .so file, easier to manage
    [language_source_path],
)
