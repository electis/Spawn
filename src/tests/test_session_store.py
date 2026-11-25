"""Session_Store on the SQLite database: CRUD, persistence, schema, and the legacy JSON import."""

import asyncio
import json
import os
import stat

import pytest

from bot.db import SCHEMA_VERSION, Database, DatabaseError
from bot.groups import GroupRegistry, GroupsError
from bot.legacy_import import import_legacy
from bot.models import SessionRecord
from bot.session_store import CorruptStoreError, SessionStore, StoreError

USER = 42


def _rec(sid="s1", name="Беседа 1", session_id="kiro-1", usage=None, persona="default"):
    return SessionRecord(
        sid=sid,
        name=name,
        session_id=session_id,
        context_usage=usage,
        created_at="2025-01-01T00:00:00Z",
        updated_at="2025-01-01T00:00:00Z",
        persona=persona,
    )


def _store(tmp_path):
    return SessionStore(Database(tmp_path / "spawn.db"))


# ---- CRUD and persistence -----------------------------------------------------------------


def test_add_activate_and_read_back_after_reopen(tmp_path):
    store = _store(tmp_path)
    asyncio.run(store.add_session(USER, _rec("a", usage=3.5)))
    asyncio.run(store.add_session(USER, _rec("b", name="Вторая")))
    asyncio.run(store.set_active(USER, "b"))

    reopened = SessionStore(Database(tmp_path / "spawn.db"))
    user = reopened.get_user(USER)
    assert user.active_sid == "b"
    assert [s.sid for s in user.sessions] == ["a", "b"]  # insertion order kept
    assert user.sessions[0].context_usage == 3.5
    assert reopened.find_session(USER, "b").name == "Вторая"


def test_unknown_user_gets_empty_record_without_writing(tmp_path):
    store = _store(tmp_path)
    user = store.get_user(999)
    assert user.active_sid is None and user.sessions == []
    assert store.find_session(999, "x") is None


def test_duplicate_sid_is_refused_and_changes_nothing(tmp_path):
    store = _store(tmp_path)
    asyncio.run(store.add_session(USER, _rec("a", name="первая")))
    with pytest.raises(StoreError):
        asyncio.run(store.add_session(USER, _rec("a", name="вторая")))
    assert store.find_session(USER, "a").name == "первая"


def test_unknown_session_operations_raise_store_error(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(StoreError):
        asyncio.run(store.set_active(USER, "missing"))
    with pytest.raises(StoreError):
        asyncio.run(store.rename_session(USER, "missing", "x"))
    with pytest.raises(StoreError):
        asyncio.run(store.set_context_usage(USER, "missing", 1.0))
    with pytest.raises(StoreError):
        asyncio.run(store.touch_session(USER, "missing", "2025-01-02T00:00:00Z"))
    with pytest.raises(StoreError):
        asyncio.run(store.remove_session(USER, "missing"))


def test_rename_usage_and_touch_update_the_row(tmp_path):
    store = _store(tmp_path)
    asyncio.run(store.add_session(USER, _rec("a")))
    asyncio.run(store.rename_session(USER, "a", "Новое имя"))
    asyncio.run(store.set_context_usage(USER, "a", 6))
    asyncio.run(store.touch_session(USER, "a", "2025-02-02T00:00:00Z"))
    rec = store.find_session(USER, "a")
    assert rec.name == "Новое имя"
    assert rec.context_usage == 6.0
    assert rec.updated_at == "2025-02-02T00:00:00Z"


def test_none_usage_is_refused_and_keeps_the_old_value(tmp_path):
    store = _store(tmp_path)
    asyncio.run(store.add_session(USER, _rec("a", usage=2.0)))
    with pytest.raises(TypeError):
        asyncio.run(store.set_context_usage(USER, "a", None))
    assert store.find_session(USER, "a").context_usage == 2.0


def test_remove_session_clears_active_pointer(tmp_path):
    store = _store(tmp_path)
    asyncio.run(store.add_session(USER, _rec("a")))
    asyncio.run(store.add_session(USER, _rec("b")))
    asyncio.run(store.set_active(USER, "a"))
    asyncio.run(store.remove_session(USER, "a"))
    user = store.get_user(USER)
    assert user.active_sid is None
    assert [s.sid for s in user.sessions] == ["b"]


def test_sessions_of_different_users_do_not_mix(tmp_path):
    store = _store(tmp_path)
    asyncio.run(store.add_session(1, _rec("same")))
    asyncio.run(store.add_session(2, _rec("same", name="чужая")))
    assert store.find_session(1, "same").name == "Беседа 1"
    assert store.find_session(2, "same").name == "чужая"


# ---- schema ---------------------------------------------------------------------------------


def test_file_database_uses_wal_and_foreign_keys(tmp_path):
    db = Database(tmp_path / "spawn.db")
    assert db.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_newer_schema_is_refused(tmp_path):
    path = tmp_path / "spawn.db"
    Database(path).conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(DatabaseError):
        Database(path)


def test_file_that_is_not_a_database_is_refused(tmp_path):
    path = tmp_path / "spawn.db"
    path.write_bytes(b"this is not sqlite" * 100)
    with pytest.raises(DatabaseError):
        Database(path)


@pytest.mark.skipif(os.name == "nt", reason="file mode check is POSIX only")
def test_database_files_are_owner_only(tmp_path):
    path = tmp_path / "spawn.db"
    store = _store(tmp_path)
    asyncio.run(store.add_session(USER, _rec("a")))
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_deleting_a_user_cascades_to_sessions(tmp_path):
    db = Database(tmp_path / "spawn.db")
    store = SessionStore(db)
    asyncio.run(store.add_session(USER, _rec("a")))
    with db.conn:
        db.conn.execute("DELETE FROM users WHERE owner_key = ?", (str(USER),))
    assert db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


# ---- legacy JSON import ---------------------------------------------------------------------


def _legacy_sessions(path, sid="a"):
    data = {
        "version": 1,
        "users": {
            str(USER): {
                "active_sid": sid,
                "sessions": [
                    {
                        "sid": sid,
                        "name": "Старая",
                        "session_id": "kiro-x",
                        "context_usage": 4.0,
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-02T00:00:00Z",
                        "persona": "writer",
                    }
                ],
            }
        },
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_import_moves_json_rows_into_the_database_once(tmp_path):
    sessions_json = tmp_path / "store.json"
    groups_json = tmp_path / "groups.json"
    _legacy_sessions(sessions_json)
    groups_json.write_text(
        json.dumps(
            {
                "version": 1,
                "groups": [{"chat_id": -100, "title": "Team", "present": True, "allowed": True}],
            }
        ),
        encoding="utf-8",
    )
    db = Database(tmp_path / "spawn.db")
    renamed = import_legacy(db, sessions_json, groups_json, seed_allowed=[-999])

    store = SessionStore(db)
    user = store.get_user(USER)
    assert user.active_sid == "a"
    assert user.sessions[0].name == "Старая"
    assert user.sessions[0].context_usage == 4.0
    assert user.sessions[0].persona == "writer"
    assert [g.chat_id for g in GroupRegistry(db).groups()] == [-100]  # seed ignored: file exists

    assert not sessions_json.exists() and (tmp_path / "store.json.migrated").exists()
    assert len(renamed) == 2

    # A second import does nothing: the flag is set and the files are gone.
    assert import_legacy(db, sessions_json, groups_json, seed_allowed=[-999]) == []
    assert SessionStore(db).find_session(USER, "a").name == "Старая"


def test_import_without_files_seeds_configured_groups(tmp_path):
    db = Database(tmp_path / "spawn.db")
    import_legacy(db, tmp_path / "none.json", tmp_path / "none-groups.json", seed_allowed=[-5, -5])
    groups = GroupRegistry(db).groups()
    assert [(g.chat_id, g.allowed) for g in groups] == [(-5, True)]


def test_corrupt_json_is_refused_and_left_in_place(tmp_path):
    sessions_json = tmp_path / "store.json"
    sessions_json.write_text("{not json", encoding="utf-8")
    db = Database(tmp_path / "spawn.db")
    with pytest.raises(CorruptStoreError):
        import_legacy(db, sessions_json, tmp_path / "groups.json")
    assert sessions_json.read_text(encoding="utf-8") == "{not json"
    assert db.get_meta("legacy_json_imported") is None  # the next start retries


def test_failed_import_writes_nothing(tmp_path):
    sessions_json = tmp_path / "store.json"
    good = {"version": 1, "users": {str(USER): {"active_sid": None, "sessions": []}}}
    bad_groups = tmp_path / "groups.json"
    bad_groups.write_text('{"version": 1, "groups": [{"chat_id": "x"}]}', encoding="utf-8")
    sessions_json.write_text(json.dumps(good), encoding="utf-8")
    db = Database(tmp_path / "spawn.db")
    with pytest.raises(GroupsError):
        import_legacy(db, sessions_json, bad_groups)
    assert db.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    assert sessions_json.exists()
