import pytest

from kvconfig import parse


def test_basic() -> None:
    assert parse("a = 1\n# comment\n\nb=two") == {"a": "1", "b": "two"}


def test_invalid_line() -> None:
    with pytest.raises(ValueError, match="invalid line"):
        parse("novalue")
