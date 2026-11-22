"""Trigger rules for group messages.

A group message reaches the bot when it:
- mentions the bot by username (@RUSpawnAI), or
- replies to a message from the bot, or
- starts with the prefix "spawn", "спаун", or "спавн" followed by one punctuation mark or space.

The prefix is matched without regard to case. It only arrives in a group when the bot's
privacy mode is off in BotFather. The prefix and its separator are removed from the prompt.
"""
from __future__ import annotations

import re

# One separator (any non-word character) after the prefix; the rest is the prompt.
_PREFIX = re.compile(r"^(?:spawn|спаун|спавн)[^\w]\s*", re.IGNORECASE)
# The prefix may also end the message, as in "spawn?": nothing is left to answer.
_PREFIX_ALONE = re.compile(r"^(?:spawn|спаун|спавн)[^\w]*$", re.IGNORECASE)


def mentions_bot(text: str, username: str | None) -> bool:
    if not username:
        return False
    pattern = re.compile(rf"(?<!\w)@{re.escape(username)}(?!\w)", re.IGNORECASE)
    return pattern.search(text) is not None


def parse_trigger(text: str, *, addressed: bool) -> str | None:
    """Return the prompt for a group message, or None when the bot should stay silent.

    addressed is True when the message mentions the bot or replies to the bot.
    """
    if _PREFIX_ALONE.match(text):
        return None
    match = _PREFIX.match(text)
    if match is not None:
        return text[match.end():].strip() or None
    return text if addressed else None
