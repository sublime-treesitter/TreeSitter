from tree_sitter import Language  # noqa: I001

# TODO: make it possible to import from src.utils
from utils import BUILD_PATH

Language.build_library(
    # Create shared object / dynamically linked library in build path
    str(BUILD_PATH / "languages.so"),
    # Include one or more languages
    [
        str(BUILD_PATH / "tree-sitter-python"),
    ],
)
