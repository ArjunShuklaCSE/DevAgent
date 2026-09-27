"""Timestamp helpers. All public functions return timezone-aware UTC datetimes."""

from datetime import datetime, timezone


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp and return it in UTC.

    Naive timestamps are assumed to already be UTC.
    """
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc)


def to_epoch(value: str) -> int:
    return int(parse_iso(value).timestamp())
