"""Property 14 (Session_Name validity) and Property 10 (context display rule)."""

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from bot.context_display import display_for, is_full
from bot.names import validate_session_name


# Feature: telegram-kiro-bot, Property 14: Session_Name validity
@settings(max_examples=200)
@given(raw=st.text(max_size=60), existing=st.lists(st.text(max_size=40), max_size=5))
def test_valid_name_is_trimmed_and_unique(raw, existing):
    result = validate_session_name(raw, existing)
    if result is None:
        return
    assert 1 <= len(result) <= 40
    assert result == raw.strip()
    assert all(other.casefold() != result.casefold() for other in existing)


@pytest.mark.parametrize(
    ("raw", "ok"),
    [("", False), ("   ", False), ("a", True), ("x" * 40, True), ("x" * 41, False)],
)
def test_name_length_boundaries(raw, ok):
    assert (validate_session_name(raw, []) is not None) is ok


def test_duplicate_name_is_rejected_case_insensitively():
    assert validate_session_name("Plan", ["plan"]) is None
    assert validate_session_name("Plan", ["Other"]) == "Plan"


# Feature: telegram-kiro-bot, Property 10: Context display rule
@settings(max_examples=200)
@given(raw=st.floats(min_value=0, max_value=100, allow_nan=False))
def test_display_rule(raw):
    shown = display_for(raw)
    if raw >= 5:
        assert shown.text == f"{raw * 10:.1f}%"
        assert shown.show_summarize
    else:
        assert shown.text is None
        assert not shown.show_summarize


@pytest.mark.parametrize("raw", [4.99, 5.0, 9.99, 10.0])
def test_display_boundaries(raw):
    shown = display_for(raw)
    assert (shown.text is not None) == (raw >= 5)


def test_unavailable_value_has_no_button():
    shown = display_for(None)
    assert shown.text == "context size unavailable"
    assert not shown.show_summarize


def test_full_state_threshold():
    assert not is_full(None)
    assert not is_full(9.99)
    assert is_full(10.0)
