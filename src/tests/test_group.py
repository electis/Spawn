"""Group mode: owner keys, trigger rules, display thresholds, routing, and group replies."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from aiogram.types import Chat, Message, User

from bot.context_display import GROUP_SHOW_THRESHOLD, SHOW_THRESHOLD, display_for
from bot.dispatcher import TextJob
from bot.gate import ContextGate
from bot.group_handlers import on_group_clear, on_group_new, on_group_text
from bot.group_trigger import mentions_bot, parse_trigger
from bot.jobs import JobProcessor
from bot.kiro_parser import RunResult
from bot.notifier import AiogramNotifier
from bot.scope import store_key
from bot.session_store import SessionStore
from bot.state import Services
from bot.ui import GROUP_FULL_TEXT, GROUP_NEW_TEXT
from tests.test_dispatch_and_jobs import FakeRunner

GROUP = -1001234567890
USER = 555
BOT_ID = 9240004342
BOT_USERNAME = "RUSpawnAI"


# ---- owner keys ------------------------------------------------------------------------


def test_private_key_is_the_user_id():
    assert store_key(USER, USER) == USER


def test_group_key_is_negative_and_unique_per_group_and_user():
    other_group = GROUP - 1
    keys = {
        store_key(GROUP, USER),
        store_key(GROUP, USER + 1),
        store_key(other_group, USER),
    }
    assert len(keys) == 3
    assert all(key < 0 for key in keys)
    assert USER not in keys


def test_group_key_rejects_out_of_range_user():
    with pytest.raises(ValueError):
        store_key(GROUP, 1 << 40)


# ---- trigger rules ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("spawn, как дела", "как дела"),
        ("Spawn? привет", "привет"),
        ("спаун: что нового", "что нового"),
        ("спавн, привет", "привет"),
        ("СПАУН! ответь", "ответь"),
        ("spawn  двойной пробел", "двойной пробел"),
    ],
)
def test_prefix_with_separator_triggers_and_is_removed(text, expected):
    assert parse_trigger(text, addressed=False) == expected


@pytest.mark.parametrize("text", ["spawn?", "спаун", "spawn ", "spawn!!"])
def test_prefix_alone_gives_no_prompt(text):
    assert parse_trigger(text, addressed=True) is None
    assert parse_trigger(text, addressed=False) is None


@pytest.mark.parametrize("text", ["spawners are fast", "спауншик", "respawn, hi"])
def test_word_that_only_starts_with_prefix_does_not_trigger(text):
    assert parse_trigger(text, addressed=False) is None


def test_plain_text_needs_an_address():
    assert parse_trigger("как дела", addressed=False) is None
    assert parse_trigger("как дела", addressed=True) == "как дела"


def test_mention_matches_username_only_as_a_whole_word():
    assert mentions_bot("@RUSpawnAI привет", BOT_USERNAME)
    assert mentions_bot("привет @ruspawnai", BOT_USERNAME)
    assert not mentions_bot("@RUSpawnAIX привет", BOT_USERNAME)
    assert not mentions_bot("привет", None)


# ---- display thresholds ----------------------------------------------------------------


def test_group_shows_usage_from_raw_7_and_private_from_raw_5():
    assert GROUP_SHOW_THRESHOLD == 7.0 and SHOW_THRESHOLD == 5.0
    assert display_for(6.9, GROUP_SHOW_THRESHOLD).text is None
    assert display_for(7.0, GROUP_SHOW_THRESHOLD).text == "70.0%"
    assert display_for(4.9).text is None
    assert display_for(5.0).text == "50.0%"


# ---- routing with fakes ----------------------------------------------------------------


class RecordingDispatcher:
    def __init__(self):
        self.jobs = []

    async def submit(self, job):
        self.jobs.append(job)


def _services(tmp_path):
    store = SessionStore(tmp_path / "sessions.json")
    dispatcher = RecordingDispatcher()
    services = Services(
        store=store,
        gate=ContextGate(store),
        dispatcher=dispatcher,
        bot_id=BOT_ID,
        bot_username=BOT_USERNAME,
    )
    return services, dispatcher


def _group_message(text, *, reply_from=None, message_id=77, from_id=USER):
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=GROUP, type="supergroup", title="team"),
        from_user=User(id=from_id, is_bot=False, first_name="A"),
        text=text,
        reply_to_message=reply_from,
    )


def _bot_reply_message():
    return Message(
        message_id=50,
        date=datetime.now(timezone.utc),
        chat=Chat(id=GROUP, type="supergroup", title="team"),
        from_user=User(id=BOT_ID, is_bot=True, first_name="Spawn"),
        text="earlier answer",
    )


def test_prefix_message_becomes_group_job_with_reply_target(tmp_path):
    services, dispatcher = _services(tmp_path)
    asyncio.run(on_group_text(_group_message("spawn, как дела"), services))
    (job,) = dispatcher.jobs
    assert isinstance(job, TextJob)
    assert job.text == "как дела"
    assert job.user_id == store_key(GROUP, USER)
    assert job.chat_id == GROUP
    assert job.reply_to == 77
    assert job.pending_name is not None and job.pending_name.startswith("Беседа ")


def test_untriggered_group_message_makes_no_job(tmp_path):
    services, dispatcher = _services(tmp_path)
    asyncio.run(on_group_text(_group_message("обычный разговор"), services))
    asyncio.run(on_group_text(_group_message("/sessions"), services))
    assert dispatcher.jobs == []


def test_reply_to_bot_message_triggers(tmp_path):
    services, dispatcher = _services(tmp_path)
    asyncio.run(on_group_text(_group_message("продолжай", reply_from=_bot_reply_message()), services))
    assert [job.text for job in dispatcher.jobs] == ["продолжай"]


def test_mention_triggers_with_text_kept(tmp_path):
    services, dispatcher = _services(tmp_path)
    asyncio.run(on_group_text(_group_message("@RUSpawnAI привет"), services))
    assert [job.text for job in dispatcher.jobs] == ["@RUSpawnAI привет"]


def test_group_new_creates_pending_session_for_that_group_user_only(tmp_path):
    class Reply:
        def __init__(self):
            self.chat = Chat(id=GROUP, type="supergroup", title="team")
            self.from_user = User(id=USER, is_bot=False, first_name="A")
            self.sent = []

        async def reply(self, text, **_):
            self.sent.append(text)

    services, _ = _services(tmp_path)
    message = Reply()
    asyncio.run(on_group_new(message, services))
    assert message.sent == [GROUP_NEW_TEXT]
    key = store_key(GROUP, USER)
    assert key in services.pending
    assert store_key(GROUP, USER + 1) not in services.pending
    assert USER not in services.pending


def test_clear_callback_starts_a_new_pending_session(tmp_path):
    class Query:
        def __init__(self):
            self.message = _group_message("x")
            self.from_user = User(id=USER, is_bot=False, first_name="A")
            self.answers = []

        async def answer(self, text=None, **_):
            self.answers.append(text)

    services, _ = _services(tmp_path)
    query = Query()
    asyncio.run(on_group_clear(query, services))
    assert store_key(GROUP, USER) in services.pending
    assert query.answers and query.answers[0]


# ---- processing and output -------------------------------------------------------------


class GroupNotifier:
    """Records what the notifier would send, without calling Telegram."""

    def __init__(self):
        self.replies = []
        self.texts = []
        self.blocked = []

    async def send_text(self, chat_id, text, reply_to=None):
        self.texts.append((chat_id, text, reply_to))

    async def send_reply(
        self, chat_id, chunks, display, choice=None, reply_to=None, group=False
    ):
        self.replies.append(
            {"chat": chat_id, "display": display, "reply_to": reply_to, "group": group}
        )

    async def send_summary(self, user_id, text, old_sid):
        raise AssertionError("summaries are not offered in groups")

    async def send_blocked(self, job):
        self.blocked.append(job)


def test_group_reply_goes_to_group_as_reply_with_group_threshold(tmp_path):
    store = SessionStore(tmp_path / "sessions.json")
    runner = FakeRunner(tmp_path, results=[RunResult("sess_00000001", "ответ", 6.5, None)])
    notifier = GroupNotifier()
    processor = JobProcessor(store, runner, notifier)
    key = store_key(GROUP, USER)
    job = TextJob(key, "aaaaaaaa", "вопрос", pending_name="Беседа aaaaaaaa", chat_id=GROUP, reply_to=77)
    asyncio.run(processor.text(job))
    (reply,) = notifier.replies
    assert reply["chat"] == GROUP
    assert reply["reply_to"] == 77
    assert reply["group"] is True
    assert reply["display"].text is None  # raw 6.5 is below the group threshold of 7
    assert store.get_user(key).active_sid == "aaaaaaaa"
    assert store.get_user(USER).sessions == []  # the private list is untouched


def test_group_blocked_message_gets_group_text_and_clear_button(tmp_path):
    class Bot:
        id = BOT_ID

        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text, **kwargs):
            self.sent.append((chat_id, text, kwargs))

    bot = Bot()
    notifier = AiogramNotifier(bot, storage=None)
    job = TextJob(store_key(GROUP, USER), "aaaaaaaa", "hi", chat_id=GROUP, reply_to=77)
    asyncio.run(notifier.send_blocked(job))
    chat_id, text, kwargs = bot.sent[0]
    assert chat_id == GROUP
    assert text == GROUP_FULL_TEXT
    assert kwargs["reply_parameters"].message_id == 77
    button = kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Очистить контекст"
    assert "Сделать выжимку" not in GROUP_FULL_TEXT



# ---- greeting on join ------------------------------------------------------------------


class _SentBot:
    id = BOT_ID

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


def _membership_event(old_status, new_status):
    from aiogram.types import ChatMemberAdministrator, ChatMemberLeft, ChatMemberMember, ChatMemberUpdated

    me = User(id=BOT_ID, is_bot=True, first_name="Spawn")
    statuses = {
        "left": lambda: ChatMemberLeft(user=me),
        "member": lambda: ChatMemberMember(user=me),
        "administrator": lambda: ChatMemberAdministrator(
            user=me,
            can_be_edited=False,
            is_anonymous=False,
            can_manage_chat=True,
            can_delete_messages=False,
            can_manage_video_chats=False,
            can_restrict_members=False,
            can_promote_members=False,
            can_change_info=False,
            can_invite_users=False,
            can_post_stories=False,
            can_edit_stories=False,
            can_delete_stories=False,
            can_send_welcome_messages=False,
        ),
    }
    return ChatMemberUpdated(
        chat=Chat(id=GROUP, type="supergroup", title="team"),
        from_user=User(id=USER, is_bot=False, first_name="A"),
        date=datetime.now(timezone.utc),
        old_chat_member=statuses[old_status](),
        new_chat_member=statuses[new_status](),
    )


@pytest.mark.parametrize(
    "old, new, greets",
    [
        ("left", "member", True),
        ("left", "administrator", True),
        ("member", "administrator", False),
        ("member", "left", False),
    ],
)
def test_greeting_only_when_bot_joins(tmp_path, old, new, greets):
    from bot.admin import GREETING_TEXT, on_my_chat_member
    from bot.groups import GroupRegistry

    services, _ = _services(tmp_path)
    services.groups = GroupRegistry(tmp_path / "groups.json")
    services.groups.load()
    bot = _SentBot()
    asyncio.run(on_my_chat_member(_membership_event(old, new), services, bot))
    if greets:
        assert bot.sent == [(GROUP, GREETING_TEXT)]
    else:
        assert bot.sent == []

# ---- character instructions ------------------------------------------------------------


class _PromptRunner:
    def __init__(self):
        self.prompts = []

    def workdir_for(self, user_id):
        return Path("/tmp")

    async def run_prompt(self, workdir, session_id, text):
        self.prompts.append(text)
        return RunResult("sess_00000002", "ответ", 1.0, None)


def test_new_session_first_message_carries_persona_and_user_text(tmp_path):
    from bot.persona import PERSONA_PROMPT

    store = SessionStore(tmp_path / "sessions.json")
    runner = _PromptRunner()
    processor = JobProcessor(store, runner, GroupNotifier())
    job = TextJob(USER, "aaaaaaaa", "Что такое благодать?", pending_name="Новая")
    asyncio.run(processor.text(job))
    (prompt,) = runner.prompts
    assert PERSONA_PROMPT in prompt
    assert prompt.endswith("Что такое благодать?")


def test_resumed_session_sends_only_the_user_text(tmp_path):
    from bot.models import SessionRecord

    store = SessionStore(tmp_path / "sessions.json")
    asyncio.run(
        store.add_session(
            USER,
            SessionRecord("aaaaaaaa", "Старая", "sess_00000001", 1.0, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
    )
    runner = _PromptRunner()
    processor = JobProcessor(store, runner, GroupNotifier())
    asyncio.run(processor.text(TextJob(USER, "aaaaaaaa", "продолжим")))
    assert runner.prompts == ["продолжим"]