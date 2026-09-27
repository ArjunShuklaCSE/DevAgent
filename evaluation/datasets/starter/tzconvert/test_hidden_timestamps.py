from datetime import datetime, timezone

from tzconvert.timestamps import parse_iso, to_epoch


def test_offset_is_converted_to_utc():
    assert parse_iso("2026-03-01T12:00:00+02:00") == datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


def test_epoch_respects_offset():
    assert to_epoch("1970-01-01T01:00:00+01:00") == 0
