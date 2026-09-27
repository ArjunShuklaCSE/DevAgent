from datetime import timezone

from tzconvert.timestamps import parse_iso, to_epoch


def test_naive_is_utc():
    assert parse_iso("2024-01-01T00:00:00").tzinfo == timezone.utc


def test_zulu_suffix():
    assert to_epoch("2024-01-01T00:00:00Z") == 1704067200
