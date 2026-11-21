"""Membership checker (Properties 2, 3), private-chat gate (Property 1)."""

import asyncio
from types import SimpleNamespace

import hypothesis.strategies as st
from aiogram.types import Chat, Message, User
from hypothesis import given, settings

from bot.membership import MEMBER_STATUSES, MembershipChecker
from bot.middleware import PrivateMembershipMiddleware

STATUSES = ["creator", "administrator", "member", "restricted", "left", "kicked"]


class FakeBot:
    """Maps group id -> status string, or an Exception to raise."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    async def get_chat_member(self, chat_id, user_id):
        self.calls.append(chat_id)
        value = self.table[chat_id]
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(status=value)


# Feature: telegram-kiro-bot, Property 2: Membership decision follows the status rule
@settings(max_examples=150)
@given(
    table=st.dictionaries(
        st.integers(min_value=-(10**12), max_value=-1),
        st.one_of(st.sampled_from(STATUSES), st.just(RuntimeError("down"))),
        min_size=1,
        max_size=6,
    )
)
def test_decision_and_no_early_exit(table):
    bot = FakeBot(table)
    checker = MembershipChecker(bot, list(table), clock=lambda: 0.0, ttl=300)
    decision = asyncio.run(checker.is_member(1))
    expected = any(
        isinstance(v, str) and v in MEMBER_STATUSES for v in table.values()
    )
    assert decision == expected
    assert sorted(bot.calls) == sorted(table)  # every group queried exactly once


# Feature: telegram-kiro-bot, Property 3: Membership cache holds positives only
def test_positive_cached_within_ttl_and_expires():
    now = {"t": 0.0}
    bot = FakeBot({-1: "member"})
    checker = MembershipChecker(bot, [-1], clock=lambda: now["t"], ttl=300)

    async def run():
        assert await checker.is_member(5)
        now["t"] = 299.0
        assert await checker.is_member(5)
        assert len(bot.calls) == 1  # served from cache
        now["t"] = 300.0
        assert await checker.is_member(5)
        assert len(bot.calls) == 2  # expired, queried again

    asyncio.run(run())


def test_negative_never_cached():
    bot = FakeBot({-1: "left", -2: RuntimeError("down")})
    checker = MembershipChecker(bot, [-1, -2], clock=lambda: 0.0, ttl=300)

    async def run():
        assert not await checker.is_member(9)
        assert not await checker.is_member(9)

    asyncio.run(run())
    assert len(bot.calls) == 4  # both groups asked on each check


def test_erroring_group_does_not_hide_a_positive_one():
    bot = FakeBot({-1: RuntimeError("down"), -2: "administrator"})
    checker = MembershipChecker(bot, [-1, -2], clock=lambda: 0.0, ttl=300)
    assert asyncio.run(checker.is_member(3))


# Feature: telegram-kiro-bot, Property 1: Non-private updates are ignored
class _Recorder:
    def __init__(self):
        self.calls = 0

    async def __call__(self, event, data):
        self.calls += 1
        return "handled"


def _message(chat_type: str) -> Message:
    return Message(
        message_id=1,
        date=0,
        chat=Chat(id=-100 if chat_type != "private" else 42, type=chat_type),
        from_user=User(id=42, is_bot=False, first_name="u"),
        text="hello",
    )


@settings(max_examples=30)
@given(chat_type=st.sampled_from(["group", "supergroup", "channel"]))
def test_non_private_updates_never_reach_handler(chat_type):
    bot = FakeBot({-1: "member"})
    checker = MembershipChecker(bot, [-1], clock=lambda: 0.0)
    middleware = PrivateMembershipMiddleware(checker)
    handler = _Recorder()
    result = asyncio.run(middleware(handler, _message(chat_type), {}))
    assert result is None
    assert handler.calls == 0
    assert bot.calls == []  # no membership call for a dropped update


def test_private_member_reaches_handler():
    bot = FakeBot({-1: "member"})
    middleware = PrivateMembershipMiddleware(MembershipChecker(bot, [-1], clock=lambda: 0.0))
    handler = _Recorder()
    assert asyncio.run(middleware(handler, _message("private"), {})) == "handled"
    assert handler.calls == 1
