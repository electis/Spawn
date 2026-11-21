"""Registry of groups the bot has been added to, and which of them are allowed.

The admin edits the allowed flag in the bot (bot/admin.py). The bot records a group
when Telegram reports a my_chat_member change for it. The file is written atomically,
with mode 0600, and a corrupt file is never overwritten.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path

GROUPS_VERSION = 1
PRESENT_STATUSES = frozenset({"member", "administrator", "creator"})


class GroupsError(Exception):
    """The groups file is corrupt or cannot be written."""


@dataclass
class GroupRecord:
    chat_id: int
    title: str
    present: bool  # the bot is currently in the group
    allowed: bool  # members of this group may use the bot


class GroupRegistry:
    def __init__(self, path: Path, seed_allowed: Iterable[int] = ()) -> None:
        self._path = Path(path)
        self._seed = tuple(dict.fromkeys(seed_allowed))
        self._groups: dict[int, GroupRecord] = {}

    def load(self) -> None:
        """Load the file. On first start, create it with the seed groups as allowed."""
        if not self._path.exists():
            self._groups = {gid: GroupRecord(gid, "", False, True) for gid in self._seed}
            self._save(self._groups)
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != GROUPS_VERSION:
                raise ValueError("unsupported groups file version")
            groups: dict[int, GroupRecord] = {}
            for item in data["groups"]:
                record = GroupRecord(
                    chat_id=int(item["chat_id"]),
                    title=str(item["title"]),
                    present=bool(item["present"]),
                    allowed=bool(item["allowed"]),
                )
                groups[record.chat_id] = record
        except (ValueError, KeyError, TypeError) as exc:
            raise GroupsError(f"groups file is corrupt: {self._path}") from exc
        self._groups = groups

    def _save(self, groups: dict[int, GroupRecord]) -> None:
        text = json.dumps(
            {
                "version": GROUPS_VERSION,
                "groups": [asdict(g) for g in sorted(groups.values(), key=lambda g: g.chat_id)],
            },
            ensure_ascii=False,
            indent=2,
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=self._path.parent, prefix=self._path.name + ".", suffix=".tmp"
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
            raise GroupsError("groups file write failed") from exc

    def _commit(self, change: Callable[[dict[int, GroupRecord]], bool]) -> bool:
        """Apply a change to a copy, save it, then commit. Returns the change's result."""
        new_groups = copy.deepcopy(self._groups)
        result = change(new_groups)
        if result:
            self._save(new_groups)
            self._groups = new_groups
        return result

    def groups(self) -> list[GroupRecord]:
        ordered = sorted(self._groups.values(), key=lambda g: (g.title.casefold(), g.chat_id))
        return [copy.copy(g) for g in ordered]

    def get(self, chat_id: int) -> GroupRecord | None:
        record = self._groups.get(chat_id)
        return None if record is None else copy.copy(record)

    def allowed_ids(self) -> tuple[int, ...]:
        return tuple(sorted(g.chat_id for g in self._groups.values() if g.allowed))

    def record_membership(self, chat_id: int, title: str, present: bool) -> None:
        """Called on my_chat_member. Keeps the admin's allowed flag."""

        def change(groups: dict[int, GroupRecord]) -> bool:
            record = groups.get(chat_id)
            if record is None:
                groups[chat_id] = GroupRecord(chat_id, title, present, False)
            else:
                record.present = present
                if title:
                    record.title = title
            return True

        self._commit(change)

    def set_allowed(self, chat_id: int, allowed: bool) -> bool:
        """Return False when the group is unknown."""

        def change(groups: dict[int, GroupRecord]) -> bool:
            record = groups.get(chat_id)
            if record is None:
                return False
            record.allowed = allowed
            return True

        return self._commit(change)
