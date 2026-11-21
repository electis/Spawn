"""Admin identity by Telegram username (ADMIN_USERNAME in the env file)."""

from __future__ import annotations


def normalize_username(value: str) -> str:
    return value.strip().lstrip("@").casefold()


def is_admin_username(username: str | None, admin_username: str | None) -> bool:
    """True when the user's username matches the configured admin. Usernames are case-insensitive."""
    if not username or not admin_username:
        return False
    return normalize_username(username) == normalize_username(admin_username)
