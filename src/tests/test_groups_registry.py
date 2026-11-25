"""Group registry on the SQLite database: membership, the admin's allowed flag, persistence."""

from bot.db import Database
from bot.groups import GroupRegistry


def _registry(tmp_path):
    return GroupRegistry(Database(tmp_path / "spawn.db"))


def test_new_group_starts_not_allowed_and_present_flag_follows_membership(tmp_path):
    registry = _registry(tmp_path)
    registry.record_membership(-100, "Team", present=True)
    record = registry.get(-100)
    assert (record.title, record.present, record.allowed) == ("Team", True, False)
    assert registry.allowed_ids() == ()

    registry.record_membership(-100, "", present=False)
    record = registry.get(-100)
    assert record.present is False
    assert record.title == "Team"  # an empty title from Telegram keeps the known one


def test_admin_allowed_flag_survives_membership_changes(tmp_path):
    registry = _registry(tmp_path)
    registry.record_membership(-100, "Team", present=True)
    assert registry.set_allowed(-100, True) is True
    registry.record_membership(-100, "Team 2", present=False)
    registry.record_membership(-100, "Team 2", present=True)
    record = registry.get(-100)
    assert record.allowed is True and record.title == "Team 2"
    assert registry.allowed_ids() == (-100,)


def test_set_allowed_on_unknown_group_returns_false(tmp_path):
    assert _registry(tmp_path).set_allowed(-7, True) is False


def test_state_is_kept_after_reopen_and_groups_are_ordered_by_title(tmp_path):
    registry = _registry(tmp_path)
    registry.record_membership(-2, "bravo", present=True)
    registry.record_membership(-1, "Alpha", present=True)
    registry.set_allowed(-2, True)

    reopened = GroupRegistry(Database(tmp_path / "spawn.db"))
    assert [g.chat_id for g in reopened.groups()] == [-1, -2]  # casefold order
    assert reopened.allowed_ids() == (-2,)
