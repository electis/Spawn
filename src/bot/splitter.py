"""Split long replies for Telegram (Requirement 2.5)."""

from __future__ import annotations

TELEGRAM_LIMIT = 4096


def split_reply(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split text into chunks of at most `limit` characters.

    A cut goes after the last newline before the limit when one exists, otherwise at
    the limit. Joining the chunks gives the original text exactly.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        newline = rest.rfind("\n", 0, limit)
        cut = newline + 1 if newline >= 0 else limit
        chunks.append(rest[:cut])
        rest = rest[cut:]
    chunks.append(rest)
    return chunks
