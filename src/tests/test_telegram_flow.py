"""Telegram flow tests through aiogram's Dispatcher.feed_update with a fake Telegram session.

One Dispatcher and one Bot are shared by the module, because the router can be attached once.
Each test uses its own User id and its own Services, so state does not leak between tests.
"""

import asyncio
import itertools
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, GetChatMember, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from aiogram.types import ChatMemberLeft, ChatMemberMember, ChatMemberUpdated, MessageEntity

from bot import ui
from bot.admin import GROUPS_TITLE
from bot.persona import first_message
from bot.admin import GroupCb, admin_router
from bot.groups import GroupRegistry
from bot.middleware import REFUSAL_TEXT
from bot.dispatcher import UserDispatcher
from bot.gate import ContextGate
from bot.handlers import router
from bot.jobs import JobProcessor
from bot.kiro_parser import RunResult
from bot.membership import MembershipChecker
from bot.middleware import PrivateMembershipMiddleware
from bot.models import SessionRecord
from bot.notifier import AiogramNotifier
from bot.session_store import SessionStore
from bot.state import Services

_ids = itertools.count(1000)


class FakeSession(BaseSession):
    """Records every Telegram method and answers without network access."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []
        self.member_status = "member"

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, GetChatMember):
            return ChatMemberMember(
                user=User(id=method.user_id, is_bot=False, first_name="u"),
                status=self.member_status,
            )
        return True

    async def close(self) -> None:
        return None

    async def stream_content(self, *args, **kwargs):  # pragma: no cover - not used
        raise NotImplementedError


class FakeRunner:
    def __init__(self, workdir: Path, results=()):
        self.workdir = workdir
        self.results = list(results)
        self.prompts: list = []

    def workdir_for(self, user_id: int) -> Path:
        return self.workdir

    async def run_prompt(self, workdir, session_id, text):
        self.prompts.append((session_id, text))
        if self.results:
            return self.results.pop(0)
        return RunResult("sess_0000aaaa", "ответ", None, None)

    async def delete_session(self, workdir, session_id):
        return None


SESSION = FakeSession()
BOT = Bot(token="123456:TESTTOKEN", session=SESSION)
STORAGE = MemoryStorage()
CHECKER = MembershipChecker(BOT, [-1001], clock=lambda: 0.0, ttl=300)
DP = Dispatcher(storage=STORAGE)
DP.message.outer_middleware(PrivateMembershipMiddleware(CHECKER, admin_username="boss"))
DP.callback_query.outer_middleware(PrivateMembershipMiddleware(CHECKER, admin_username="boss"))
DP.include_router(admin_router)
DP.include_router(router)


def _services(tmp_path: Path, results=()) -> tuple[Services, FakeRunner]:
    store = SessionStore(tmp_path / "store.json")
    asyncio.run(store.load())
    runner = FakeRunner(tmp_path, results)
    notifier = AiogramNotifier(BOT, STORAGE)
    processor = JobProcessor(store, runner, notifier)
    gate = ContextGate(store)
    dispatcher = UserDispatcher(
        processor.handle,
        is_blocked=gate.blocks,
        on_blocked=notifier.send_blocked,
    )
    groups = GroupRegistry(tmp_path / "groups.json")
    groups.load()
    services = Services(
        store=store,
        gate=gate,
        dispatcher=dispatcher,
        groups=groups,
        membership=CHECKER,
        admin_username="boss",
    )
    return services, runner


def _user(uid: int) -> User:
    return User(id=uid, is_bot=False, first_name="u")


def _text_update(uid: int, text: str, chat_type: str = "private") -> Update:
    chat_id = uid if chat_type == "private" else -1001
    message = Message(
        message_id=next(_ids),
        date=0,
        chat=Chat(id=chat_id, type=chat_type),
        from_user=_user(uid),
        text=text,
    )
    return Update(update_id=next(_ids), message=message)


def _callback_update(uid: int, data: str) -> Update:
    query = CallbackQuery(
        id=str(next(_ids)),
        from_user=_user(uid),
        chat_instance="ci",
        data=data,
        message=Message(
            message_id=next(_ids),
            date=0,
            chat=Chat(id=uid, type="private"),
            from_user=_user(uid),
            text="menu",
        ),
    )
    return Update(update_id=next(_ids), callback_query=query)


async def _feed(update: Update, services: Services) -> None:
    SESSION.calls.clear()
    await DP.feed_update(BOT, update, services=services)
    await services.dispatcher.drain()


def _sent() -> list[str]:
    return [m.text for m in SESSION.calls if isinstance(m, SendMessage)]


def _session(sid: str, name: str, usage=None) -> SessionRecord:
    return SessionRecord(sid, name, "sess_0000aaaa", usage, "t0", "t0")


def test_group_update_is_ignored(tmp_path):
    services, runner = _services(tmp_path)
    SESSION.member_status = "member"
    asyncio.run(_feed(_text_update(next(_ids), "hi", chat_type="supergroup"), services))
    assert SESSION.calls == []
    assert runner.prompts == []


def test_non_member_is_refused_without_kiro(tmp_path):
    services, runner = _services(tmp_path)
    SESSION.member_status = "left"
    uid = next(_ids)
    asyncio.run(_feed(_text_update(uid, "hi"), services))
    assert _sent() == [REFUSAL_TEXT]
    assert runner.prompts == []


def test_first_text_opens_a_conversation_automatically(tmp_path):
    services, runner = _services(tmp_path)
    SESSION.member_status = "member"
    asyncio.run(_feed(_text_update(next(_ids), "hi"), services))
    assert ui.NO_ACTIVE not in _sent()
    assert [text for _, text in runner.prompts] == [first_message("hi")]  # first message of a new Session


def test_new_conversation_round_trip(tmp_path):
    services, runner = _services(tmp_path)
    SESSION.member_status = "member"
    uid = next(_ids)

    asyncio.run(_feed(_callback_update(uid, ui.MenuCb(action="new").pack()), services))
    assert ui.ASK_NAME in _sent()

    asyncio.run(_feed(_text_update(uid, "Проект"), services))
    assert ui.NEW_READY.format(name="Проект") in _sent()
    assert runner.prompts == []  # the name alone sends no request

    asyncio.run(_feed(_text_update(uid, "Привет"), services))
    assert runner.prompts == [(None, first_message("Привет"))]
    assert "ответ" in _sent()
    user = services.store.get_user(uid)
    assert [s.name for s in user.sessions] == ["Проект"]
    assert user.active_sid is not None


def test_invalid_name_keeps_state_and_sends_no_request(tmp_path):
    services, runner = _services(tmp_path)
    SESSION.member_status = "member"
    uid = next(_ids)
    asyncio.run(_feed(_callback_update(uid, ui.MenuCb(action="new").pack()), services))
    asyncio.run(_feed(_text_update(uid, "   "), services))
    assert ui.NAME_INVALID in _sent()
    assert runner.prompts == []


def test_open_session_sets_active_locally(tmp_path):
    services, runner = _services(tmp_path)
    SESSION.member_status = "member"
    uid = next(_ids)
    asyncio.run(services.store.add_session(uid, _session("aaaaaaaa", "Notes")))
    asyncio.run(
        _feed(_callback_update(uid, ui.SessionCb(action="o", sid="aaaaaaaa").pack()), services)
    )
    assert ui.OPEN_TEXT.format(name="Notes") in _sent()
    assert services.store.get_user(uid).active_sid == "aaaaaaaa"
    assert runner.prompts == []


def test_full_session_blocks_text_and_offers_options(tmp_path):
    services, runner = _services(tmp_path)
    SESSION.member_status = "member"
    uid = next(_ids)
    asyncio.run(services.store.add_session(uid, _session("aaaaaaaa", "Full", usage=12.0)))
    asyncio.run(services.store.set_active(uid, "aaaaaaaa"))
    asyncio.run(_feed(_text_update(uid, "ещё"), services))
    assert ui.FULL_TEXT in _sent()
    assert runner.prompts == []


def test_empty_list_message(tmp_path):
    services, _ = _services(tmp_path)
    SESSION.member_status = "member"
    uid = next(_ids)
    asyncio.run(_feed(_callback_update(uid, ui.MenuCb(action="list").pack()), services))
    assert ui.EMPTY_LIST in _sent()


def test_summary_flow_end_to_end(tmp_path):
    services, runner = _services(
        tmp_path,
        results=[
            RunResult("sess_0000aaaa", "Выжимка: факты", 3.0, None),  # summary of old session
            RunResult("sess_0000bbbb", "Ответ в новой сессии", 1.0, None),  # first reply of new
        ],
    )
    SESSION.member_status = "member"
    uid = next(_ids)
    asyncio.run(services.store.add_session(uid, _session("aaaaaaaa", "Old")))
    asyncio.run(services.store.set_active(uid, "aaaaaaaa"))

    asyncio.run(_feed(_callback_update(uid, ui.MenuCb(action="sum").pack()), services))
    assert "Выжимка: факты" in _sent()
    assert runner.prompts[0][0] == "sess_0000aaaa"  # summary goes to the old Session

    asyncio.run(_feed(_text_update(uid, "Новая"), services))
    assert runner.prompts[1] == (None, first_message("Выжимка: факты"))  # new Session, no --resume-id
    assert [s.name for s in services.store.get_user(uid).sessions] == ["Old", "Новая"]
    assert ui.CHOICE_TEXT in _sent()
    assert "Ответ в новой сессии" in _sent()

# ---- Admin (ADMIN_USERNAME) -------------------------------------------------------------

ADMIN_UID = 777001


def _admin_message(text: str, username: str = "boss") -> Update:
    user = User(id=ADMIN_UID, is_bot=False, first_name="a", username=username)
    entities = [MessageEntity(type="bot_command", offset=0, length=len(text))] if text.startswith("/") else None
    message = Message(
        message_id=next(_ids),
        date=0,
        chat=Chat(id=ADMIN_UID, type="private"),
        from_user=user,
        text=text,
        entities=entities,
    )
    return Update(update_id=next(_ids), message=message)


def _admin_callback(data: str, username: str = "boss") -> Update:
    user = User(id=ADMIN_UID, is_bot=False, first_name="a", username=username)
    query = CallbackQuery(
        id=str(next(_ids)),
        from_user=user,
        chat_instance="ci",
        data=data,
        message=Message(
            message_id=next(_ids), date=0, chat=Chat(id=ADMIN_UID, type="private"), from_user=user, text="groups"
        ),
    )
    return Update(update_id=next(_ids), callback_query=query)


def _sent_markups() -> list:
    return [m.reply_markup for m in SESSION.calls if isinstance(m, SendMessage)]


def _button_texts(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


def test_admin_lists_groups_with_toggle_state(tmp_path):
    services, _ = _services(tmp_path)
    SESSION.member_status = "left"  # the admin is not a member, and still gets through
    services.groups.record_membership(-1001, "Dev chat", True)
    services.groups.record_membership(-1002, "Old chat", False)
    services.groups.set_allowed(-1002, True)
    asyncio.run(_feed(_admin_message("/admin"), services))

    assert ui.NO_ACTIVE not in _sent()
    texts = _button_texts(_sent_markups()[-1])
    assert "⬜ Dev chat" in texts
    assert "✅ Old chat (бота нет)" in texts


def test_admin_toggle_allows_present_group(tmp_path):
    services, _ = _services(tmp_path)
    services.groups.record_membership(-1001, "Dev chat", True)
    asyncio.run(_feed(_admin_callback(GroupCb(action="t", chat_id=-1001).pack()), services))
    assert services.groups.get(-1001).allowed is True
    assert -1001 in services.groups.allowed_ids()
    asyncio.run(_feed(_admin_callback(GroupCb(action="t", chat_id=-1001).pack()), services))
    assert services.groups.get(-1001).allowed is False


def test_admin_cannot_allow_group_bot_is_not_in(tmp_path):
    services, _ = _services(tmp_path)
    services.groups.record_membership(-1003, "Gone", False)
    asyncio.run(_feed(_admin_callback(GroupCb(action="t", chat_id=-1003).pack()), services))
    assert services.groups.get(-1003).allowed is False


def test_non_admin_admin_command_is_ordinary_text(tmp_path):
    services, runner = _services(tmp_path)
    SESSION.member_status = "member"
    asyncio.run(_feed(_admin_message("/admin", username="someone"), services))
    assert GROUPS_TITLE not in _sent()  # no admin list is shown
    assert [text for _, text in runner.prompts] == [first_message("/admin")]  # ordinary text


def test_non_admin_cannot_toggle_group(tmp_path):
    services, _ = _services(tmp_path)
    services.groups.record_membership(-1001, "Dev chat", True)
    asyncio.run(
        _feed(_admin_callback(GroupCb(action="t", chat_id=-1001).pack(), username="someone"), services)
    )
    assert services.groups.get(-1001).allowed is False


def test_bot_added_and_removed_updates_presence(tmp_path):
    services, _ = _services(tmp_path)
    bot_user = User(id=BOT.id, is_bot=True, first_name="kiro", username="kiro_bot")
    group = Chat(id=-1004, type="supergroup", title="Team")
    added = Update(
        update_id=next(_ids),
        my_chat_member=ChatMemberUpdated(
            chat=group,
            from_user=_user(ADMIN_UID),
            date=0,
            old_chat_member=ChatMemberLeft(user=bot_user),
            new_chat_member=ChatMemberMember(user=bot_user, status="member"),
        ),
    )
    asyncio.run(DP.feed_update(BOT, added, services=services))
    assert services.groups.get(-1004).present is True
    assert services.groups.get(-1004).title == "Team"

    removed = Update(
        update_id=next(_ids),
        my_chat_member=ChatMemberUpdated(
            chat=group,
            from_user=_user(ADMIN_UID),
            date=0,
            old_chat_member=ChatMemberMember(user=bot_user, status="member"),
            new_chat_member=ChatMemberLeft(user=bot_user),
        ),
    )
    asyncio.run(DP.feed_update(BOT, removed, services=services))
    assert services.groups.get(-1004).present is False
# ---- /context ---------------------------------------------------------------------------


def _command_update(uid: int, text: str) -> Update:
    message = Message(
        message_id=next(_ids),
        date=0,
        chat=Chat(id=uid, type="private"),
        from_user=_user(uid),
        text=text,
        entities=[MessageEntity(type="bot_command", offset=0, length=len(text))],
    )
    return Update(update_id=next(_ids), message=message)


def test_context_shows_value_below_show_threshold(tmp_path):
    services, runner = _services(tmp_path)
    SESSION.member_status = "member"
    uid = next(_ids)
    asyncio.run(services.store.add_session(uid, _session("aaaaaaaa", "Quiet", usage=0.2)))
    asyncio.run(services.store.set_active(uid, "aaaaaaaa"))
    asyncio.run(_feed(_command_update(uid, "/context"), services))
    assert ui.CONTEXT_VALUE.format(name="Quiet", value="2.0%") in _sent()
    assert runner.prompts == []


def test_context_in_full_state_shows_value_and_note(tmp_path):
    services, runner = _services(tmp_path)
    SESSION.member_status = "member"
    uid = next(_ids)
    asyncio.run(services.store.add_session(uid, _session("aaaaaaaa", "Big", usage=12.0)))
    asyncio.run(services.store.set_active(uid, "aaaaaaaa"))
    asyncio.run(_feed(_command_update(uid, "/context"), services))
    sent = _sent()
    expected = ui.CONTEXT_VALUE.format(name="Big", value="120.0%") + "\n" + ui.CONTEXT_FULL_NOTE
    assert sent == [expected]
    assert runner.prompts == []


def test_context_without_value_says_unknown(tmp_path):
    services, _ = _services(tmp_path)
    SESSION.member_status = "member"
    uid = next(_ids)
    asyncio.run(services.store.add_session(uid, _session("aaaaaaaa", "New", usage=None)))
    asyncio.run(services.store.set_active(uid, "aaaaaaaa"))
    asyncio.run(_feed(_command_update(uid, "/context"), services))
    assert ui.CONTEXT_UNKNOWN.format(name="New") in _sent()


def test_context_without_active_session(tmp_path):
    services, _ = _services(tmp_path)
    SESSION.member_status = "member"
    asyncio.run(_feed(_command_update(next(_ids), "/context"), services))
    assert _sent() == [ui.NO_ACTIVE]


def test_context_command_is_not_taken_as_session_name(tmp_path):
    services, _ = _services(tmp_path)
    SESSION.member_status = "member"
    uid = next(_ids)
    asyncio.run(_feed(_callback_update(uid, ui.MenuCb(action="new").pack()), services))
    asyncio.run(_feed(_command_update(uid, "/context"), services))
    assert ui.NO_ACTIVE in _sent()  # answered as a command, the name prompt is still active
    assert services.store.get_user(uid).sessions == []
# ---- Command menu ------------------------------------------------------------------------


def test_command_menu_names_are_valid():
    import re

    from bot.ui import BOT_COMMANDS

    names = [c.command for c in BOT_COMMANDS]
    assert {"new", "sessions", "summary", "context"} <= set(names)
    assert all(re.fullmatch(r"[a-z0-9_]{1,32}", n) for n in names)


def test_new_command_asks_for_name_and_takes_it(tmp_path):
    services, runner = _services(tmp_path)
    SESSION.member_status = "member"
    uid = next(_ids)
    asyncio.run(_feed(_command_update(uid, "/new"), services))
    assert ui.ASK_NAME in _sent()
    asyncio.run(_feed(_text_update(uid, "Команда"), services))
    assert ui.NEW_READY.format(name="Команда") in _sent()


def test_sessions_command_shows_empty_message(tmp_path):
    services, _ = _services(tmp_path)
    SESSION.member_status = "member"
    asyncio.run(_feed(_command_update(next(_ids), "/sessions"), services))
    assert ui.EMPTY_LIST in _sent()


def test_summary_command_without_active_session(tmp_path):
    services, runner = _services(tmp_path)
    SESSION.member_status = "member"
    asyncio.run(_feed(_command_update(next(_ids), "/summary"), services))
    assert _sent() == [ui.NO_ACTIVE]
    assert runner.prompts == []
def test_start_command_shows_text_and_command_keyboard(tmp_path):
    from aiogram.types import ReplyKeyboardMarkup

    services, runner = _services(tmp_path)
    SESSION.member_status = "member"
    asyncio.run(_feed(_command_update(next(_ids), "/start"), services))
    assert ui.START_TEXT in _sent()
    markup = [m.reply_markup for m in SESSION.calls if isinstance(m, SendMessage)][-1]
    assert isinstance(markup, ReplyKeyboardMarkup)
    labels = [b.text for row in markup.keyboard for b in row]
    assert labels == ["/new", "/sessions", "/summary", "/context"]
    assert runner.prompts == []