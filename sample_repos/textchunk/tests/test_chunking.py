import pytest

from textchunk import chunk, sliding_windows


def test_even_split():
    assert chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]


def test_rejects_non_positive_size():
    with pytest.raises(ValueError):
        chunk([1], 0)


def test_sliding_windows():
    assert sliding_windows("abcd", 2) == [["a", "b"], ["b", "c"], ["c", "d"]]
