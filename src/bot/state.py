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
