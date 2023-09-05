from __future__ import annotations

import sys
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


REPO_ROOT = Path(__file__).parent.parent
DEPS_PATH = REPO_ROOT / "deps"
BUILD_PATH = REPO_ROOT / "build"


def not_none(var: T | None) -> T:
    """
    This narrows type from `T | None` -> `T`.
    """
    assert var is not None
    return var


def maybe_none(var: T) -> T | None:
    return var


def add_path(path: str):
    if path not in sys.path:
        sys.path.insert(0, path)
