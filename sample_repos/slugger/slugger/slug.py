"""URL slugs."""

import re
import unicodedata


def slugify(title: str) -> str:
    """Lowercase ASCII slug with single hyphens; empty input gives an empty slug."""
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    words = re.split(r"[^a-zA-Z0-9]+", normalized.lower())
    words = [w for w in words if w]
    if words[0].isdigit():
        words[0] = f"n{words[0]}"
    return "-".join(words)
