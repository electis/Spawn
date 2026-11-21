"""Session_Name validation (Requirements 5.2, 5.3; Assumption A-2)."""

from __future__ import annotations

from collections.abc import Iterable

MAX_NAME_LENGTH = 40


def validate_session_name(raw: str, existing: Iterable[str]) -> str | None:
    """Return the trimmed name, or None when it is invalid."""
    name = raw.strip()
    if not 1 <= len(name) <= MAX_NAME_LENGTH:
        return None
    folded = name.casefold()
    if any(other.casefold() == folded for other in existing):
        return None
    return name
