"""Session_Name validation (Requirements 5.2, 5.3; Assumption A-2)."""

from __future__ import annotations

from collections.abc import Iterable

MAX_NAME_LENGTH = 40


_TITLE_STRIP = " \t\"'«»“”`*#.:"


def clean_title(raw: str) -> str:
    """Return the first line of a model reply, without quotes, markup, or trailing punctuation."""
    for line in raw.splitlines():
        cleaned = line.strip().strip(_TITLE_STRIP)
        if cleaned:
            return cleaned
    return ""


def session_title(name: str, persona_name: str) -> str:
    """Return the stored Session name: the user's name with the character's name as postfix."""
    return f"{name} ({persona_name})" if persona_name else name


def validate_session_name(raw: str, existing: Iterable[str]) -> str | None:
    """Return the trimmed name, or None when it is invalid."""
    name = raw.strip()
    if not 1 <= len(name) <= MAX_NAME_LENGTH:
        return None
    folded = name.casefold()
    if any(other.casefold() == folded for other in existing):
        return None
    return name
