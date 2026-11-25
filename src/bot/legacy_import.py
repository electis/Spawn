"""One-time import of the legacy JSON stores (sessions.json and groups.json) into the database.

The import runs once per database. It is recorded in the meta table, so later starts skip it.
All rows are written in one transaction. The JSON files are renamed to *.migrated only after
the commit, so a failed import leaves them where they were and the next start retries.
Without a groups file, the configured group ids are seeded as allowed.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable
from pathlib import Path

from bot.db import Database
from bot.groups import GroupRecord, decode_legacy_groups
from bot.models import UserRecord
from bot.session_store import CorruptStoreError

logger = logging.getLogger(__name__)

IMPORT_FLAG = "legacy_json_imported"
SESSIONS_VERSION = 1


def decode_legacy_users(text: str) -> dict[int, UserRecord]:
    """Parse the version 1 session_store JSON format. Raise CorruptStoreError when invalid."""
    try:
        data = json.loads(text)
        if not isinstance(data, dict) or data.get("version") != SESSIONS_VERSION:
            raise ValueError("unsupported store version")
        users = data.get("users")
        if not isinstance(users, dict):
            raise ValueError("users must be an object")
        return {int(key): UserRecord.from_dict(value) for key, value in users.items()}
    except (ValueError, KeyError, TypeError) as exc:
        raise CorruptStoreError("session store JSON is corrupt") from exc


def _read_optional(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def import_legacy(
    db: Database, sessions_path: Path, groups_path: Path, seed_allowed: Iterable[int] = ()
) -> list[Path]:
    """Import the JSON stores if this database has not imported them yet.

    Return the JSON files that were renamed to *.migrated (empty when nothing was done).
    Raise CorruptStoreError or GroupsError, writing nothing, when a file cannot be parsed.
    """
    if db.get_meta(IMPORT_FLAG) is not None:
        return []

    sessions_text = _read_optional(Path(sessions_path))
    groups_text = _read_optional(Path(groups_path))
    users = {} if sessions_text is None else decode_legacy_users(sessions_text)
    groups: list[GroupRecord]
    if groups_text is None:
        groups = [GroupRecord(gid, "", False, True) for gid in dict.fromkeys(seed_allowed)]
    else:
        groups = decode_legacy_groups(groups_text)

    conn = db.conn
    with conn:
        for owner_key, user in users.items():
            conn.execute(
                "INSERT INTO users (owner_key, active_sid) VALUES (?, ?)",
                (str(owner_key), user.active_sid),
            )
            for session in user.sessions:
                conn.execute(
                    "INSERT INTO sessions (owner_key, sid, name, session_id, context_usage, "
                    "created_at, updated_at, persona) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(owner_key),
                        session.sid,
                        session.name,
                        session.session_id,
                        session.context_usage,
                        session.created_at,
                        session.updated_at,
                        session.persona,
                    ),
                )
        for group in groups:
            conn.execute(
                "INSERT INTO group_chats (chat_id, title, present, allowed) VALUES (?, ?, ?, ?)",
                (group.chat_id, group.title, int(group.present), int(group.allowed)),
            )
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)", (IMPORT_FLAG, "1")
        )

    renamed: list[Path] = []
    for path in (Path(sessions_path), Path(groups_path)):
        if not path.exists():
            continue
        target = path.with_name(path.name + ".migrated")
        try:
            os.replace(path, target)
            renamed.append(target)
        except OSError as exc:
            # The rows are committed, so a leftover JSON file is harmless. Report it and go on.
            logger.warning("could not rename %s after import: %s", path, type(exc).__name__)
    logger.info("imported legacy JSON stores into the database (%d users, %d groups)",
                len(users), len(groups))
    return renamed
