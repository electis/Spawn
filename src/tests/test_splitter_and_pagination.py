"""Property 6 (reply splitting) and Property 16 (pagination)."""

import hypothesis.strategies as st
from hypothesis import given, settings

from bot.models import SessionRecord
from bot.pagination import PAGE_SIZE, page_of
from bot.splitter import split_reply


# Feature: telegram-kiro-bot, Property 6: Reply splitting is lossless and bounded
@settings(max_examples=200)
@given(text=st.text(max_size=300), limit=st.integers(min_value=1, max_value=50))
def test_split_is_lossless_and_bounded(text, limit):
    chunks = split_reply(text, limit)
    assert all(len(c) <= limit for c in chunks)
    assert "".join(chunks) == text


def test_split_prefers_last_newline_before_limit():
    text = "aaaa\nbbbb\ncccc"
    assert split_reply(text, 10) == ["aaaa\nbbbb\n", "cccc"]


def test_split_hard_cut_without_newline():
    assert split_reply("abcdefgh", 3) == ["abc", "def", "gh"]


def test_split_short_text_is_one_chunk():
    assert split_reply("hi", 4096) == ["hi"]


def _record(sid: str, updated: str) -> SessionRecord:
    return SessionRecord(
        sid=sid,
        name=sid,
        session_id="sess_00000000",
        context_usage=None,
        created_at="2026-01-01T00:00:00Z",
        updated_at=updated,
    )


sessions_strategy = st.lists(
    st.tuples(
        st.from_regex(r"[0-9a-f]{8}", fullmatch=True),
        st.integers(min_value=0, max_value=20).map(lambda n: f"2026-01-01T00:00:{n:02d}Z"),
    ),
    unique_by=lambda t: t[0],
    max_size=40,
).map(lambda pairs: [_record(sid, ts) for sid, ts in pairs])


# Feature: telegram-kiro-bot, Property 16: Pagination partitions the Session list
@settings(max_examples=200)
@given(sessions=sessions_strategy)
def test_pages_partition_the_list(sessions):
    expected = sorted(
        sorted(sessions, key=lambda s: s.sid), key=lambda s: s.updated_at, reverse=True
    )
    collected = []
    total_pages = max(1, -(-len(sessions) // PAGE_SIZE))
    for index in range(total_pages):
        page = page_of(sessions, index)
        assert len(page.items) <= PAGE_SIZE
        assert page.has_prev == (index > 0)
        assert page.has_next == (index < total_pages - 1)
        collected.extend(page.items)
    assert collected == expected


def test_page_boundaries_for_exactly_five_and_six():
    five = [_record(f"{i:08x}", "2026-01-01T00:00:00Z") for i in range(5)]
    page = page_of(five, 0)
    assert len(page.items) == 5 and not page.has_next and not page.has_prev

    six = five + [_record("ffffffff", "2026-01-01T00:00:01Z")]
    first = page_of(six, 0)
    assert len(first.items) == 5 and first.has_next
    assert first.items[0].sid == "ffffffff"  # most recent first
    second = page_of(six, 1)
    assert len(second.items) == 1 and second.has_prev and not second.has_next


def test_empty_list_gives_one_empty_page():
    page = page_of([], 3)
    assert page.items == [] and page.page == 0 and not page.has_next
