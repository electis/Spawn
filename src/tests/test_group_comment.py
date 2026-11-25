"""/comment in allowed groups: argument parsing, message selection, prompt, and routing."""

import asyncio
import itertools
from datetime import datetime, timedelta, timezone

import pytest
from aiogram.filters import CommandObject
from aiogram.types import Chat, Message, User

from bot.db import Database
from bot.dispatcher import TextJob
from bot.group_comment import (
    EMPTY_TEXT,
    MAX_MESSAGES,
    TOO_MANY_TEXT,
    USAGE_TEXT,
    CommentRequest,
    CommentRequestError,
    GroupLog,
    GroupMessage,
    build_prompt,
    parse_request,
    select_messages,
)
from bot.group_handlers import on_group_comment, on_group_text
from bot.scope import store_key
from tests.test_group import GROUP, USER, _GroupMessage, _group_message, _services

_IDS = itertools.count(1000)  # unique message ids, as Telegram gives per chat
NOW = datetime(2025, 6, 10, 12, 0, tzinfo=timezone.utc)


def _msg(i, *, hours_ago=0.0, author="Ann", text=None):
    return GroupMessage(
        message_id=i,
        when=NOW - timedelta(hours=hours_ago),
        author=author,
        text=text if text is not None else f"text {i}",
    )


# ---- argument parsing ------------------------------------------------------------------


@pytest.mark.parametrize("arg, expected", [("10", 10), (" 1 ", 1), ("100", 100)])
def test_count_argument(arg, expected):
    assert parse_request(arg) == CommentRequest(count=expected)


def test_day_and_hour_arguments_become_periods():
    assert parse_request("2d") == CommentRequest(period=timedelta(days=2))
    assert parse_request("5H") == CommentRequest(period=timedelta(hours=5))


@pytest.mark.parametrize("arg", [None, "", "abc", "2m", "1.5h", "-3", "0", "0d", "101"])
def test_bad_arguments_are_rejected(arg):
    with pytest.raises(CommentRequestError):
        parse_request(arg)


def test_count_above_limit_reports_too_many():
    with pytest.raises(CommentRequestError, match="too_many"):
        parse_request(str(MAX_MESSAGES + 1))


# ---- selection -------------------------------------------------------------------------


def test_count_takes_the_last_n_in_order():
    messages = [_msg(i) for i in range(1, 6)]
    chosen = select_messages(messages, CommentRequest(count=2), NOW)
    assert [m.message_id for m in chosen] == [4, 5]


def test_count_larger_than_buffer_takes_all():
    messages = [_msg(1), _msg(2)]
    chosen = select_messages(messages, CommentRequest(count=10), NOW)
    assert [m.message_id for m in chosen] == [1, 2]


def test_period_keeps_only_messages_inside_it():
    messages = [_msg(1, hours_ago=50), _msg(2, hours_ago=30), _msg(3, hours_ago=1)]
    chosen = select_messages(messages, CommentRequest(period=timedelta(days=2)), NOW)
    assert [m.message_id for m in chosen] == [2, 3]


def test_period_returns_every_message_inside_it_in_order():
    messages = [_msg(i, hours_ago=0.1 * (300 - i)) for i in range(1, 301)]
    chosen = select_messages(messages, CommentRequest(period=timedelta(days=2)), NOW)
    assert len(chosen) == 300
    assert chosen[-1].message_id == 300


def test_group_log_survives_a_restart(tmp_path):
    path = tmp_path / "spawn.db"
    log = GroupLog(Database(path))
    for i in range(1, 4):
        log.record(GROUP, _msg(i, text=f"до перезапуска {i}"))
    restarted = GroupLog(Database(path))  # a new process opens the same file
    texts = [m.text for m in restarted.messages(GROUP)]
    assert texts == ["до перезапуска 1", "до перезапуска 2", "до перезапуска 3"]


def test_group_log_keeps_at_most_limit_per_group():
    log = GroupLog(Database(":memory:"))
    for i in range(MAX_MESSAGES + 5):
        log.record(GROUP, _msg(i))
    log.record(GROUP - 1, _msg(999))
    kept = log.messages(GROUP)
    assert len(kept) == MAX_MESSAGES
    assert kept[0].message_id == 5
    assert [m.message_id for m in log.messages(GROUP - 1)] == [999]


# ---- prompt ----------------------------------------------------------------------------


def test_prompt_lists_copied_messages_in_order():
    prompt = build_prompt([_msg(1, author="Ann", text="привет"), _msg(2, author="Bob", text="да")])
    assert "Ann: привет\nBob: да" in prompt
    assert prompt.startswith("Прокомментируй")


# ---- routing ---------------------------------------------------------------------------


def _run(services, text_or_none, arg=None):
    """Post a group message, or run /comment with arg. Returns the fake message used."""
    if arg is None:
        asyncio.run(on_group_text(_group_message(text_or_none, message_id=next(_IDS)), services))
        return None
    message = _GroupMessage()
    message.message_id = 88  # the /comment message itself, which replies target
    asyncio.run(on_group_comment(message, services, CommandObject(command="comment", args=arg)))
    return message


def test_bot_commands_and_bot_messages_are_not_recorded(tmp_path):
    services, _ = _services(tmp_path)
    _run(services, "первое")
    _run(services, "/sessions")
    bot_message = Message(
        message_id=78,
        date=datetime.now(timezone.utc),
        chat=Chat(id=GROUP, type="supergroup", title="team"),
        from_user=User(id=9240004342, is_bot=True, first_name="Spawn"),
        text="ответ бота",
    )
    asyncio.run(on_group_text(bot_message, services))
    texts = [m.text for m in services.group_log.messages(GROUP)]
    assert texts == ["первое"]


def test_comment_sends_last_messages_as_a_prompt_in_the_members_session(tmp_path):
    services, dispatcher = _services(tmp_path)
    for text in ["раз", "два", "три"]:
        _run(services, text)
    message = _run(services, None, arg="2")
    assert message.sent == []
    (job,) = dispatcher.jobs
    assert isinstance(job, TextJob)
    assert job.user_id == store_key(GROUP, USER)
    assert job.chat_id == GROUP
    assert "A: два\nA: три" in job.text
    assert "раз" not in job.text


def test_comment_without_messages_says_so_and_submits_nothing(tmp_path):
    services, dispatcher = _services(tmp_path)
    message = _run(services, None, arg="5h")
    assert message.sent == [(EMPTY_TEXT, None)]
    assert dispatcher.jobs == []


@pytest.mark.parametrize("arg", ["abc", "0", "101"])
def test_bad_argument_replies_and_submits_nothing(tmp_path, arg):
    services, dispatcher = _services(tmp_path)
    _run(services, "есть сообщение")
    message = _run(services, None, arg=arg)
    expected = TOO_MANY_TEXT if arg == "101" else USAGE_TEXT
    assert message.sent == [(expected, None)]
    assert dispatcher.jobs == []


def test_period_over_the_buffer_limit_uses_the_latest_hundred(tmp_path):
    services, dispatcher = _services(tmp_path)
    for i in range(MAX_MESSAGES + 1):
        _run(services, f"сообщение {i}")
    message = _run(services, None, arg="2d")
    assert message.sent == []
    (job,) = dispatcher.jobs
    assert "A: сообщение 0\n" not in job.text
    assert job.text.endswith(f"A: сообщение {MAX_MESSAGES}")
