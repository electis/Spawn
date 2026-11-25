"""Registry of groups the bot has been added to, and which of them are allowed.

The admin edits the allowed flag in the bot (bot/admin.py). The bot records a group
when Telegram reports a my_chat_member change for it. The records live in the bot
database (table group_chats). The legacy groups.json file is read once, by
bot/legacy_import.py.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from bot.db import Database

GROUPS_VERSION = 1
PRESENT_STATUSES = frozenset({"member", "administrator", "creator"})


class GroupsError(Exception):
    """The legacy groups file is corrupt."""


@dataclass
class GroupRecord:
    chat_id: int
    title: str
    present: bool  # the bot is currently in the group
    allowed: bool  # members of this group may use the bot


def decode_legacy_groups(text: str) -> list[GroupRecord]:
    """Parse the version 1 groups.json format. Raise GroupsError when it is not valid."""
    try:
        data = json.loads(text)
        if not isinstance(data, dict) or data.get("version") != GROUPS_VERSION:
            raise ValueError("unsupported groups file version")
        return [
            GroupRecord(
                chat_id=int(item["chat_id"]),
                title=str(item["title"]),
                present=bool(item["present"]),
                allowed=bool(item["allowed"]),
            )
            for item in data["groups"]
        ]
    except (ValueError, KeyError, TypeError) as exc:
        raise GroupsError("groups file is corrupt") from exc


def _record(row: sqlite3.Row) -> GroupRecord:
    return GroupRecord(
        chat_id=row["chat_id"],
        title=row["title"],
        present=bool(row["present"]),
        allowed=bool(row["allowed"]),
    )


class GroupRegistry:
    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def _conn(self) -> sqlite3.Connection:
        return self._db.conn

    def groups(self) -> list[GroupRecord]:
        rows = self._conn.execute(
            "SELECT chat_id, title, present, allowed FROM group_chats "
            "ORDER BY lower(title), chat_id"
        ).fetchall()
        return [_record(row) for row in rows]

    def get(self, chat_id: int) -> GroupRecord | None:
        row = self._conn.execute(
            "SELECT chat_id, title, present, allowed FROM group_chats WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        return None if row is None else _record(row)

    def allowed_ids(self) -> tuple[int, ...]:
        rows = self._conn.execute(
            "SELECT chat_id FROM group_chats WHERE allowed = 1 ORDER BY chat_id"
        ).fetchall()
        return tuple(row["chat_id"] for row in rows)

    def record_membership(self, chat_id: int, title: str, present: bool) -> None:
        """Called on my_chat_member. Keeps the admin's allowed flag and any known title."""
        with self._conn:
            self._conn.execute(
                "INSERT INTO group_chats (chat_id, title, present, allowed) VALUES (?, ?, ?, 0) "
                "ON CONFLICT(chat_id) DO UPDATE SET "
                "present = excluded.present, "
                "title = CASE WHEN excluded.title <> '' THEN excluded.title ELSE group_chats.title END",
                (chat_id, title, int(present)),
            )

    def set_allowed(self, chat_id: int, allowed: bool) -> bool:
        """Return False when the group is unknown."""
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE group_chats SET allowed = ? WHERE chat_id = ?", (int(allowed), chat_id)
            )
        return cursor.rowcount == 1
