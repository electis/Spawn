"""A private User with no conversations gets one opened automatically (on /start or first text)."""

import asyncio
from types import SimpleNamespace

from bot.dispatcher import UserDispatcher
from bot.gate import ContextGate
from bot.handlers import _ensure_first_session, on_command_start
from bot.models import SessionRecord
from bot.session_store import SessionStore
from bot.state import Services

USER = 77


class _Message(SimpleNamespace):
    def __init__(self, user_id):
        super().__init__(from_user=SimpleNamespace(id=user_id), sent=[])

    async def answer(self, text, **_):
        self.sent.append(text)


def _services(tmp_path):
    store = SessionStore(tmp_path / "sessions.json")

    async def handle(job):
        return None

    return Services(store=store, gate=ContextGate(store), dispatcher=UserDispatcher(handle))


def test_start_opens_one_pending_conversation(tmp_path):
    services = _services(tmp_path)
    asyncio.run(on_command_start(_Message(USER), services))
    pending = services.pending[USER]
    assert pending.name.startswith("Беседа ")
    before = pending.sid
    asyncio.run(on_command_start(_Message(USER), services))  # second /start keeps the same one
    assert services.pending[USER].sid == before


def test_user_with_existing_conversations_gets_none_opened(tmp_path):
    services = _services(tmp_path)
    record = SessionRecord("aaaaaaaa", "Старая", "sess_00000001", None, "t", "t")
    asyncio.run(services.store.add_session(USER, record))
    asyncio.run(services.store.set_active(USER, "aaaaaaaa"))
    assert _ensure_first_session(services, USER) is False
    assert USER not in services.pending


def test_first_conversation_is_not_duplicated_after_it_is_stored(tmp_path):
    services = _services(tmp_path)
    assert _ensure_first_session(services, USER) is True
    assert _ensure_first_session(services, USER) is False  # the pending one counts as active
