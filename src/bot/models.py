"""Data models for the Session_Store (design: Data models)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

_STR_FIELDS = ("sid", "name", "session_id", "created_at", "updated_at")


@dataclass
class SessionRecord:
    sid: str
    name: str
    session_id: str
    context_usage: float | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sid": self.sid,
            "name": self.name,
            "session_id": self.session_id,
            "context_usage": self.context_usage,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SessionRecord:
        for key in _STR_FIELDS:
            if not isinstance(data[key], str):
                raise ValueError(f"{key} must be a string")
        usage = data["context_usage"]
        if usage is not None and (
            isinstance(usage, bool) or not isinstance(usage, (int, float))
        ):
            raise ValueError("context_usage must be a number or null")
        return cls(
            sid=data["sid"],
            name=data["name"],
            session_id=data["session_id"],
            context_usage=None if usage is None else float(usage),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )


@dataclass
class UserRecord:
    active_sid: str | None = None
    sessions: list[SessionRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_sid": self.active_sid,
            "sessions": [s.to_dict() for s in self.sessions],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> UserRecord:
        active = data["active_sid"]
        if active is not None and not isinstance(active, str):
            raise ValueError("active_sid must be a string or null")
        sessions = data["sessions"]
        if not isinstance(sessions, list):
            raise ValueError("sessions must be a list")
        return cls(
            active_sid=active,
            sessions=[SessionRecord.from_dict(item) for item in sessions],
        )
