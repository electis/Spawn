"""A private User with no conversations gets one opened automatically (on /start or first text)."""

import asyncio
from types import SimpleNamespace

from bot.db import Database
from bot.dispatcher import UserDispatcher
from bot.gate import ContextGate
from bot.handlers import _ensure_first_session, on_command_start
from bot.models import SessionRecord
from bot.session_store import SessionStore
from bot import ui
from bot.state import Services, SessionNameStates
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

USER = 77


class _Message(SimpleNamespace):
    def __init__(self, user_id):
        super().__init__(from_user=SimpleNamespace(id=user_id), sent=[])

    async def answer(self, text, **_):
        self.sent.append(text)


def _services(tmp_path):
    store = SessionStore(Database(tmp_path / "spawn.db"))

    async def handle(job):
        return None

    return Services(store=store, gate=ContextGate(store), dispatcher=UserDispatcher(handle))


def test_start_asks_for_a_character_and_opens_nothing_yet(tmp_path):
    services = _services(tmp_path)
    state = FSMContext(
        storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=USER, user_id=USER)
    )
    message = _Message(USER)
    asyncio.run(on_command_start(message, state))
    assert services.pending == {}  # the conversation opens after the character is picked
    assert message.sent == [ui.PERSONA_CHOICE_TEXT, ui.COMMANDS_HINT]
    assert asyncio.run(state.get_state()) == SessionNameStates.awaiting_persona.state


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
