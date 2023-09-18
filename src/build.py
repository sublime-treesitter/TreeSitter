"""
This can be called with a system python (not Sublime's python) in a subprocess to build .so files.
"""
import sys

from tree_sitter import Language

language_file = sys.argv[1]
language_path = sys.argv[2]

Language.build_library(
    # Create shared object / dynamically linked library in build path
    language_file,
    # Include just one language per .so file, easier to manage
    [language_path],
)
