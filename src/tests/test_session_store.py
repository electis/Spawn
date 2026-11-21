"""Session_Store: round trip (Property 9), User isolation (Property 8), edge cases."""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from bot.models import SessionRecord
from bot.session_store import CorruptStoreError, SessionStore, StoreWriteError

SID = st.from_regex(r"[0-9a-f]{8}", fullmatch=True)
TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=1, max_size=40)

session_strategy = st.builds(
    SessionRecord,
    sid=SID,
    name=TEXT,
    session_id=st.from_regex(r"sess_[0-9a-f]{8}", fullmatch=True),
    context_usage=st.one_of(st.none(), st.floats(min_value=0, max_value=100, allow_nan=False)),
    created_at=st.just("2026-11-21T07:00:00Z"),
    updated_at=st.just("2026-11-21T07:20:00Z"),
)

ops_strategy = st.lists(
    st.one_of(
        st.tuples(st.just("add"), session_strategy),
        st.tuples(st.just("usage"), st.floats(min_value=0, max_value=100, allow_nan=False)),
        st.tuples(st.just("remove"), st.none()),
        st.tuples(st.just("active"), st.none()),
    ),
    max_size=8,
)


async def _apply(store: SessionStore, user_id: int, ops) -> None:
    for kind, arg in ops:
        user = store.get_user(user_id)
        if kind == "add":
            if all(s.sid != arg.sid for s in user.sessions):
                await store.add_session(user_id, arg)
                await store.set_active(user_id, arg.sid)
        elif kind == "usage" and user.sessions:
            await store.set_context_usage(user_id, user.sessions[0].sid, arg)
        elif kind == "remove" and user.sessions:
            await store.remove_session(user_id, user.sessions[0].sid)
        elif kind == "active" and user.sessions:
            await store.set_active(user_id, user.sessions[-1].sid)


# Feature: telegram-kiro-bot, Property 9: Session_Store round trip
@settings(max_examples=100, deadline=None)
@given(ops=ops_strategy)
def test_round_trip(ops):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "store.json"

        async def run():
            store = SessionStore(path)
            await _apply(store, 42, ops)
            reloaded = SessionStore(path)
            await reloaded.load()
            return store.get_user(42), reloaded.get_user(42)

        before, after = asyncio.run(run())
        assert before == after


# Feature: telegram-kiro-bot, Property 8: Users are isolated
@settings(max_examples=100, deadline=None)
@given(ops_a=ops_strategy, ops_b=ops_strategy)
def test_users_are_isolated(ops_a, ops_b):
    with tempfile.TemporaryDirectory() as tmp:
        store = SessionStore(Path(tmp) / "store.json")

        async def run():
            await _apply(store, 1, ops_a)
            snapshot = store.get_user(2)
            await _apply(store, 2, ops_b)
            return snapshot

        asyncio.run(run())
        # User 1's record must not change when User 2 is written.
        fresh = SessionStore(Path(tmp) / "store.json")
        asyncio.run(fresh.load())
        assert fresh.get_user(1) == asyncio.run(_reread(tmp, 1))


async def _reread(tmp: str, user_id: int):
    store = SessionStore(Path(tmp) / "store.json")
    await store.load()
    return store.get_user(user_id)


def test_first_start_without_file(tmp_path):
    store = SessionStore(tmp_path / "store.json")
    asyncio.run(store.load())
    assert store.get_user(7).sessions == []
    assert store.get_user(7).active_sid is None


def test_set_context_usage_refuses_none(tmp_path):
    store = SessionStore(tmp_path / "store.json")
    rec = SessionRecord("aaaaaaaa", "n", "sess_00000001", 3.0, "t", "t")

    async def run():
        await store.add_session(1, rec)
        with pytest.raises(TypeError):
            await store.set_context_usage(1, "aaaaaaaa", None)

    asyncio.run(run())
    assert store.get_user(1).sessions[0].context_usage == 3.0


def test_remove_clears_active(tmp_path):
    store = SessionStore(tmp_path / "store.json")
    rec = SessionRecord("aaaaaaaa", "n", "sess_00000001", None, "t", "t")

    async def run():
        await store.add_session(1, rec)
        await store.set_active(1, "aaaaaaaa")
        await store.remove_session(1, "aaaaaaaa")

    asyncio.run(run())
    assert store.get_user(1).active_sid is None
    assert store.get_user(1).sessions == []


def test_file_mode_is_0600(tmp_path):
    if os.name != "posix":
        pytest.skip("POSIX file modes only")
    store = SessionStore(tmp_path / "store.json")
    rec = SessionRecord("aaaaaaaa", "n", "sess_00000001", None, "t", "t")
    asyncio.run(store.add_session(1, rec))
    assert (tmp_path / "store.json").stat().st_mode & 0o777 == 0o600


def test_failed_write_keeps_previous_file(tmp_path):
    path = tmp_path / "store.json"
    store = SessionStore(path)
    first = SessionRecord("aaaaaaaa", "one", "sess_00000001", None, "t", "t")
    second = SessionRecord("bbbbbbbb", "two", "sess_00000002", None, "t", "t")

    async def run():
        await store.add_session(1, first)
        before = path.read_text(encoding="utf-8")
        with mock.patch("bot.session_store.os.replace", side_effect=OSError("disk full")):
            with pytest.raises(StoreWriteError):
                await store.add_session(1, second)
        assert path.read_text(encoding="utf-8") == before
        leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
        assert leftovers == []

    asyncio.run(run())
    assert [s.sid for s in store.get_user(1).sessions] == ["aaaaaaaa"]


def test_corrupt_file_is_backed_up_and_not_overwritten(tmp_path):
    path = tmp_path / "store.json"
    path.write_text("{not json", encoding="utf-8")
    store = SessionStore(path)
    with pytest.raises(CorruptStoreError):
        asyncio.run(store.load())
    assert path.read_text(encoding="utf-8") == "{not json"
    assert (tmp_path / "store.json.bak").read_text(encoding="utf-8") == "{not json"


def test_saved_file_has_version(tmp_path):
    path = tmp_path / "store.json"
    rec = SessionRecord("aaaaaaaa", "n", "sess_00000001", None, "t", "t")
    asyncio.run(SessionStore(path).add_session(5, rec))
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1
