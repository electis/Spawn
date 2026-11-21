"""Group registry: seeding, persistence, membership updates, admin toggles, and corruption."""

import json
import os

import pytest

from bot.groups import GroupRegistry, GroupsError
from bot.membership import MembershipChecker
from bot.roles import is_admin_username


def test_first_start_seeds_allowed_groups(tmp_path):
    registry = GroupRegistry(tmp_path / "groups.json", seed_allowed=[-1, -2, -1])
    registry.load()
    assert registry.allowed_ids() == (-2, -1)
    assert all(not g.present for g in registry.groups())


def test_seed_is_ignored_when_file_exists(tmp_path):
    path = tmp_path / "groups.json"
    first = GroupRegistry(path, seed_allowed=[-1])
    first.load()
    second = GroupRegistry(path, seed_allowed=[-5])
    second.load()
    assert second.allowed_ids() == (-1,)


def test_changes_survive_reload(tmp_path):
    path = tmp_path / "groups.json"
    registry = GroupRegistry(path)
    registry.load()
    registry.record_membership(-100, "Dev", True)
    assert registry.set_allowed(-100, True)
    reloaded = GroupRegistry(path)
    reloaded.load()
    record = reloaded.get(-100)
    assert record.title == "Dev" and record.present and record.allowed


def test_membership_update_keeps_allowed_flag(tmp_path):
    registry = GroupRegistry(tmp_path / "groups.json")
    registry.load()
    registry.record_membership(-100, "Dev", True)
    registry.set_allowed(-100, True)
    registry.record_membership(-100, "", False)  # bot removed
    record = registry.get(-100)
    assert record.present is False and record.allowed is True
    assert record.title == "Dev"


def test_unknown_group_cannot_be_set(tmp_path):
    registry = GroupRegistry(tmp_path / "groups.json")
    registry.load()
    assert registry.set_allowed(-999, True) is False
    assert registry.allowed_ids() == ()


@pytest.mark.skipif(os.name != "posix", reason="mode check is POSIX only")
def test_file_mode_is_0600(tmp_path):
    registry = GroupRegistry(tmp_path / "groups.json")
    registry.load()
    registry.record_membership(-1, "x", True)
    assert (tmp_path / "groups.json").stat().st_mode & 0o777 == 0o600


def test_corrupt_file_is_not_overwritten(tmp_path):
    path = tmp_path / "groups.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(GroupsError):
        GroupRegistry(path).load()
    assert path.read_text(encoding="utf-8") == "{broken"


def test_unsupported_version_is_corrupt(tmp_path):
    path = tmp_path / "groups.json"
    path.write_text(json.dumps({"version": 9, "groups": []}), encoding="utf-8")
    with pytest.raises(GroupsError):
        GroupRegistry(path).load()


def test_membership_checker_follows_callable_group_list():
    class Bot:
        def __init__(self):
            self.asked = []

        async def get_chat_member(self, chat_id, user_id):
            self.asked.append(chat_id)
            return type("M", (), {"status": "member"})()

    import asyncio

    current = {"groups": (-1,)}
    bot = Bot()
    checker = MembershipChecker(bot, lambda: current["groups"], clock=lambda: 0.0)
    asyncio.run(checker.is_member(1))
    assert bot.asked == [-1]
    current["groups"] = (-2,)
    checker.clear()
    asyncio.run(checker.is_member(1))
    assert bot.asked == [-1, -2]


@pytest.mark.parametrize(
    ("username", "admin", "expected"),
    [
        ("Boss", "boss", True),
        ("@boss", "BOSS", True),
        ("other", "boss", False),
        (None, "boss", False),
        ("boss", None, False),
        ("", "", False),
    ],
)
def test_admin_username_matching(username, admin, expected):
    assert is_admin_username(username, admin) is expected
