"""Session_Store: per-User Sessions in one JSON file with atomic writes.

Requirements 3.2, 3.3, 3.5, 4.5, 7.4, 7.5.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import shutil
import tempfile
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from bot.models import SessionRecord, UserRecord

logger = logging.getLogger(__name__)

STORE_VERSION = 1


class StoreError(Exception):
    """Base error for Session_Store operations."""


class CorruptStoreError(StoreError):
    """The store file exists but cannot be parsed. The file is never overwritten."""


class StoreWriteError(StoreError):
    """An atomic write failed. The previous file is kept."""


def _encode(users: dict[int, UserRecord]) -> str:
    data = {
        "version": STORE_VERSION,
        "users": {str(uid): rec.to_dict() for uid, rec in users.items()},
    }
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def _decode(text: str) -> dict[int, UserRecord]:
    data: Any = json.loads(text)
    if not isinstance(data, dict) or data.get("version") != STORE_VERSION:
        raise ValueError("unsupported or missing store version")
    users = data.get("users")
    if not isinstance(users, dict):
        raise ValueError("users must be an object")
    return {int(key): UserRecord.from_dict(value) for key, value in users.items()}


class SessionStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._users: dict[int, UserRecord] = {}
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    async def load(self) -> None:
        async with self._lock:
            if not self._path.exists():
                self._users = {}
                return
            text = self._path.read_text(encoding="utf-8")
            try:
                users = _decode(text)
            except (ValueError, KeyError, TypeError) as exc:
                backup = self._path.with_name(self._path.name + ".bak")
                shutil.copy2(self._path, backup)
                logger.error("session store is corrupt; copied to %s", backup)
                raise CorruptStoreError(f"session store is corrupt: {self._path}") from exc
            self._users = users

    def get_user(self, user_id: int) -> UserRecord:
        """Return a copy of the User's record. An unknown User gets an empty record."""
        return copy.deepcopy(self._users.get(user_id, UserRecord()))

    def _write(self, users: dict[int, UserRecord]) -> None:
        text = _encode(users)
        directory = self._path.parent
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=directory, prefix=self._path.name + ".", suffix=".tmp"
        )
        try:
            os.chmod(tmp_name, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self._path)
        except OSError as exc:
            with suppress(OSError):
                os.unlink(tmp_name)
            logger.error("session store write failed: %s", exc)
            raise StoreWriteError("session store write failed") from exc

    async def _mutate(self, change: Callable[[dict[int, UserRecord]], Any]) -> Any:
        """Apply a change to a copy, write it, then commit it. A failed write changes nothing."""
        async with self._lock:
            new_users = copy.deepcopy(self._users)
            result = change(new_users)
            self._write(new_users)
            self._users = new_users
            return result

    async def add_session(self, user_id: int, rec: SessionRecord) -> None:
        def change(users: dict[int, UserRecord]) -> None:
            user = users.setdefault(user_id, UserRecord())
            if any(s.sid == rec.sid for s in user.sessions):
                raise StoreError(f"session {rec.sid} already exists")
            user.sessions.append(rec)

        await self._mutate(change)

    async def set_active(self, user_id: int, sid: str | None) -> None:
        def change(users: dict[int, UserRecord]) -> None:
            user = users.setdefault(user_id, UserRecord())
            if sid is not None and not any(s.sid == sid for s in user.sessions):
                raise StoreError(f"unknown session {sid}")
            user.active_sid = sid

        await self._mutate(change)

    async def rename_session(self, user_id: int, sid: str, name: str) -> None:
        def change(users: dict[int, UserRecord]) -> None:
            user = users.get(user_id)
            session = None
            if user is not None:
                session = next((s for s in user.sessions if s.sid == sid), None)
            if session is None:
                raise StoreError(f"unknown session {sid}")
            session.name = name

        await self._mutate(change)

    async def set_context_usage(self, user_id: int, sid: str, usage: float) -> None:
        """Replace one Session's raw usage. None is refused so a value is never reset (4.5)."""
        if usage is None or isinstance(usage, bool) or not isinstance(usage, (int, float)):
            raise TypeError("context usage must be a number; None is not stored")

        def change(users: dict[int, UserRecord]) -> None:
            user = users.get(user_id)
            session = None
            if user is not None:
                session = next((s for s in user.sessions if s.sid == sid), None)
            if session is None:
                raise StoreError(f"unknown session {sid}")
            session.context_usage = float(usage)

        await self._mutate(change)

    async def touch_session(self, user_id: int, sid: str, when: str) -> None:
        """Set updated_at, the time of the last message in the Session (Requirement 6.1)."""

        def change(users: dict[int, UserRecord]) -> None:
            user = users.get(user_id)
            session = None
            if user is not None:
                session = next((s for s in user.sessions if s.sid == sid), None)
            if session is None:
                raise StoreError(f"unknown session {sid}")
            session.updated_at = when

        await self._mutate(change)

    def find_session(self, user_id: int, sid: str) -> SessionRecord | None:
        user = self._users.get(user_id)
        if user is None:
            return None
        found = next((s for s in user.sessions if s.sid == sid), None)
        return None if found is None else copy.deepcopy(found)

    async def remove_session(self, user_id: int, sid: str) -> None:
        def change(users: dict[int, UserRecord]) -> None:
            user = users.get(user_id)
            if user is None:
                raise StoreError(f"unknown session {sid}")
            remaining = [s for s in user.sessions if s.sid != sid]
            if len(remaining) == len(user.sessions):
                raise StoreError(f"unknown session {sid}")
            user.sessions = remaining
            if user.active_sid == sid:
                user.active_sid = None

        await self._mutate(change)
