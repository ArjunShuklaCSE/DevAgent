"""Split sequences into fixed-size pieces."""

from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def chunk(items: Sequence[T], size: int) -> list[list[T]]:
    """Split ``items`` into consecutive lists of length ``size``.

    The last chunk may be shorter than ``size``.
    """
    if size <= 0:
        raise ValueError("size must be positive")
    count = len(items) // size
    return [list(items[i * size : (i + 1) * size]) for i in range(count)]


def sliding_windows(items: Sequence[T], width: int) -> list[list[T]]:
    """All contiguous windows of length ``width``."""
    if width <= 0:
        raise ValueError("width must be positive")
    return [list(items[i : i + width]) for i in range(len(items) - width + 1)]
