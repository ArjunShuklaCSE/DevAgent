from textchunk import chunk


def test_last_partial_chunk_is_kept():
    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_shorter_than_size():
    assert chunk("ab", 5) == [["a", "b"]]
