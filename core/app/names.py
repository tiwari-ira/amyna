"""Patient names as they should be spoken or shown."""

import re

_DIGITS = re.compile(r"\d+")


def spoken_name(name: str | None) -> str | None:
    """The record's name without the digits synthetic records append.

    Synthetic health records (Synthea) number every name part, e.g.
    "Chi716 Greenfelder433". People's names never contain digits, so removing
    them recovers the name without guessing. None when nothing usable is left.
    """
    if not name:
        return None
    parts = [_DIGITS.sub("", part) for part in name.split()]
    cleaned = " ".join(part for part in parts if part.strip(".-'"))
    return cleaned or None


def first_name(name: str | None) -> str | None:
    cleaned = spoken_name(name)
    return cleaned.split()[0] if cleaned else None
