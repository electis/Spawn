"""Shared state: FSM states, in-memory pending Sessions, and the services container."""

from __future__ import annotations

from dataclasses import dataclass, field

from aiogram.fsm.state import State, StatesGroup

from bot.dispatcher import UserDispatcher
from bot.gate import ContextGate
from bot.groups import GroupRegistry
from bot.membership import MembershipChecker
from bot.session_store import SessionStore


class SessionNameStates(StatesGroup):
    awaiting_name: State = State()


@dataclass
class PendingSession:
    """A new Session that is not stored until its first successful reply (Assumption A-12)."""

    sid: str
    name: str
    summary_of: str | None = None


@dataclass
class Services:
    store: SessionStore
    gate: ContextGate
    dispatcher: UserDispatcher
    pending: dict[int, PendingSession] = field(default_factory=dict)
    groups: GroupRegistry | None = None
    membership: MembershipChecker | None = None
    admin_username: str | None = None
    bot_id: int | None = None
    bot_username: str | None = None

    def group_is_open(self, chat_id: int) -> bool:
        """A group answers only when the admin allowed it and the bot is still in it."""
        if self.groups is None:
            return False
        record = self.groups.get(chat_id)
        return record is not None and record.allowed and record.present


def reconcile_pending(services: Services, key: int) -> PendingSession | None:
    """Drop the pending Session once the first reply has stored it."""
    pending = services.pending.get(key)
    if pending is not None and services.store.find_session(key, pending.sid) is not None:
        services.pending.pop(key, None)
        return None
    return pending


def active_sid(services: Services, key: int) -> str | None:
    pending = reconcile_pending(services, key)
    if pending is not None:
        return pending.sid
    return services.store.get_user(key).active_sid
