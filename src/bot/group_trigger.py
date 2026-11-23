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
from functools import lru_cache

from bot.persona import DEFAULT_PERSONA, Persona


@lru_cache(maxsize=None)
def _prefix_patterns(names: tuple[str, ...]) -> tuple[re.Pattern[str], re.Pattern[str]]:
    """Patterns for the character's name prefix, built from the character's names."""
    if not names:
        never = re.compile(r"(?!)")  # a character without names never matches a prefix
        return never, never
    alternation = "|".join(re.escape(name) for name in names)
    # One separator (any non-word character) after the prefix; the rest is the prompt.
    prefix = re.compile(rf"^(?:{alternation})[^\w]\s*", re.IGNORECASE)
    # The prefix may also end the message, as in "spawn?": nothing is left to answer.
    prefix_alone = re.compile(rf"^(?:{alternation})[^\w]*$", re.IGNORECASE)
    return prefix, prefix_alone


def mentions_bot(text: str, username: str | None) -> bool:
    if not username:
        return False
    pattern = re.compile(rf"(?<!\w)@{re.escape(username)}(?!\w)", re.IGNORECASE)
    return pattern.search(text) is not None


def parse_trigger(
    text: str, *, addressed: bool, persona: Persona = DEFAULT_PERSONA
) -> str | None:
    """Return the prompt for a group message, or None when the bot should stay silent.

    addressed is True when the message mentions the bot or replies to the bot.
    persona supplies the names that count as a prefix trigger.
    """
    prefix, prefix_alone = _prefix_patterns(persona.names)
    if prefix_alone.match(text):
        return None
    match = prefix.match(text)
    if match is not None:
        return text[match.end():].strip() or None
    return text if addressed else None
