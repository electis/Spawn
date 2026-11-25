"""Session_Store: per-User Sessions in the bot database (tables users and sessions).

Requirements 3.2, 3.3, 3.5, 4.5, 7.4, 7.5. Each change is one SQLite transaction. A failed
change, including a StoreError for an unknown Session, leaves the stored data as it was.
The owner key (bot/scope.py) is stored as text, because group keys do not fit in 64 bits.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.db import Database
from bot.models import SessionRecord, UserRecord


class StoreError(Exception):
    """Base error for Session_Store operations."""


class CorruptStoreError(StoreError):
    """The legacy JSON store cannot be parsed. Its file is left in place."""


_SESSION_COLUMNS = "sid, name, session_id, context_usage, created_at, updated_at, persona"


def _key(user_id: int) -> str:
    return str(user_id)


def _session(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        sid=row["sid"],
        name=row["name"],
        session_id=row["session_id"],
        context_usage=row["context_usage"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        persona=row["persona"],
    )


class SessionStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def _conn(self) -> sqlite3.Connection:
        return self._db.conn

    def get_user(self, user_id: int) -> UserRecord:
        """Return the User's record. An unknown User gets an empty record."""
        user = self._conn.execute(
            "SELECT active_sid FROM users WHERE owner_key = ?", (_key(user_id),)
        ).fetchone()
        if user is None:
            return UserRecord()
        rows = self._conn.execute(
            f"SELECT {_SESSION_COLUMNS} FROM sessions WHERE owner_key = ? ORDER BY rowid",
            (_key(user_id),),
        ).fetchall()
        return UserRecord(active_sid=user["active_sid"], sessions=[_session(r) for r in rows])

    def find_session(self, user_id: int, sid: str) -> SessionRecord | None:
        row = self._conn.execute(
            f"SELECT {_SESSION_COLUMNS} FROM sessions WHERE owner_key = ? AND sid = ?",
            (_key(user_id), sid),
        ).fetchone()
        return None if row is None else _session(row)

    def _ensure_user(self, user_id: int) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO users (owner_key, active_sid) VALUES (?, NULL)",
            (_key(user_id),),
        )

    def _update_session(self, user_id: int, sid: str, values: dict[str, Any]) -> None:
        """Set columns on one Session. An unknown Session is a StoreError."""
        assignments = ", ".join(f"{column} = ?" for column in values)
        cursor = self._conn.execute(
            f"UPDATE sessions SET {assignments} WHERE owner_key = ? AND sid = ?",
            (*values.values(), _key(user_id), sid),
        )
        if cursor.rowcount == 0:
            raise StoreError(f"unknown session {sid}")

    async def add_session(self, user_id: int, rec: SessionRecord) -> None:
        with self._conn:
            self._ensure_user(user_id)
            try:
                self._conn.execute(
                    "INSERT INTO sessions (owner_key, sid, name, session_id, context_usage, "
                    "created_at, updated_at, persona) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        _key(user_id),
                        rec.sid,
                        rec.name,
                        rec.session_id,
                        rec.context_usage,
                        rec.created_at,
                        rec.updated_at,
                        rec.persona,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise StoreError(f"session {rec.sid} already exists") from exc

    async def set_active(self, user_id: int, sid: str | None) -> None:
        with self._conn:
            self._ensure_user(user_id)
            if sid is not None and self.find_session(user_id, sid) is None:
                raise StoreError(f"unknown session {sid}")
            self._conn.execute(
                "UPDATE users SET active_sid = ? WHERE owner_key = ?", (sid, _key(user_id))
            )

    async def rename_session(self, user_id: int, sid: str, name: str) -> None:
        with self._conn:
            self._update_session(user_id, sid, {"name": name})

    async def set_context_usage(self, user_id: int, sid: str, usage: float) -> None:
        """Replace one Session's raw usage. None is refused so a value is never reset (4.5)."""
        if usage is None or isinstance(usage, bool) or not isinstance(usage, (int, float)):
            raise TypeError("context usage must be a number; None is not stored")
        with self._conn:
            self._update_session(user_id, sid, {"context_usage": float(usage)})

    async def touch_session(self, user_id: int, sid: str, when: str) -> None:
        """Set updated_at, the time of the last message in the Session (Requirement 6.1)."""
        with self._conn:
            self._update_session(user_id, sid, {"updated_at": when})

    async def remove_session(self, user_id: int, sid: str) -> None:
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM sessions WHERE owner_key = ? AND sid = ?", (_key(user_id), sid)
            )
            if cursor.rowcount == 0:
                raise StoreError(f"unknown session {sid}")
            self._conn.execute(
                "UPDATE users SET active_sid = NULL WHERE owner_key = ? AND active_sid = ?",
                (_key(user_id), sid),
            )
