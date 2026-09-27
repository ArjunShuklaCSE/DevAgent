"""Parse ``key = value`` configuration text."""


def parse(text: str) -> dict[str, str]:
    """Return a mapping of keys to values.

    Blank lines and lines starting with ``#`` are ignored. Whitespace around keys and
    values is stripped. Values may contain any characters.
    """
    result: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("=")
        if len(parts) < 2:
            raise ValueError(f"invalid line: {raw!r}")
        result[parts[0].strip()] = parts[1].strip()
    return result
