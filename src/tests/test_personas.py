"""Characters: the Persona base class, the registry, names of Sessions, and stored character keys."""

import asyncio

import pytest

from bot.gate import ContextGate
from bot.group_trigger import parse_trigger
from bot.models import SessionRecord
from bot.names import clean_title, session_title
from bot.persona import (
    DEFAULT_PERSONA,
    PERSONAS,
    Persona,
    SpawnPersona,
    WriterPersona,
    get_persona,
    resolve_persona,
)
from bot.session_store import SessionStore, StoreError
from bot.state import Services, persona_for


def test_writer_is_a_persona_subclass_registered_under_its_key():
    assert issubclass(WriterPersona, Persona)
    assert PERSONAS["writer"].key == "writer"
    assert get_persona("writer").display_name == "Писатель"


def test_writer_prompt_holds_only_prohibitions():
    prompt = WriterPersona.prompt
    assert prompt.startswith("Do not")
    assert "Спаун" not in prompt and "Spawn" not in prompt
    for line in prompt.splitlines():
        assert line.startswith(("Do not", "Never", "You are not")), line


def test_writer_inherits_neutral_defaults_from_the_base_class():
    writer = WriterPersona()
    assert writer.refusal_replies == Persona.refusal_replies
    assert writer.greeting_text == ""
    assert writer.start_text == ""


def test_writer_first_message_wraps_the_user_text_with_its_prompt():
    message = get_persona("writer").first_message("привет")
    assert WriterPersona.prompt in message
    assert message.endswith("привет")


def test_base_persona_without_prompt_sends_the_text_unchanged():
    assert Persona().first_message("привет") == "привет"


def test_spawn_stays_the_default_character():
    assert DEFAULT_PERSONA.key == SpawnPersona.key == "spawn"


def test_resolve_persona_falls_back_to_default_for_unknown_or_missing_keys():
    assert resolve_persona("removed-character") is DEFAULT_PERSONA
    assert resolve_persona(None) is DEFAULT_PERSONA
    assert resolve_persona("writer") is PERSONAS["writer"]


def test_get_persona_rejects_an_unknown_key():
    with pytest.raises(KeyError):
        get_persona("removed-character")


def test_writer_has_no_name_prefix_in_groups():
    writer = get_persona("writer")
    assert parse_trigger("spawn привет", addressed=False, persona=writer) is None
    assert parse_trigger("spawn привет", addressed=True, persona=writer) == "spawn привет"


def test_session_title_adds_the_character_postfix():
    assert session_title("Чай", "Писатель") == "Чай (Писатель)"
    assert session_title("Чай", "") == "Чай"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('"Проект Альфа".', "Проект Альфа"),
        ("**Вопрос о благодати**", "Вопрос о благодати"),
        ("\n\n  «Чай и книги»\nпояснение", "Чай и книги"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_clean_title_keeps_only_the_first_usable_line(raw, expected):
    assert clean_title(raw) == expected


def _record(**overrides) -> dict:
    data = {
        "sid": "aaaaaaaa",
        "name": "Чай (Спаун)",
        "session_id": "sess_00000001",
        "context_usage": None,
        "created_at": "t0",
        "updated_at": "t0",
    }
    data.update(overrides)
    return data


def test_session_record_keeps_its_character_through_storage():
    record = SessionRecord.from_dict(_record(persona="writer"))
    assert record.persona == "writer"
    assert SessionRecord.from_dict(record.to_dict()).persona == "writer"


def test_session_record_without_persona_key_uses_the_default_character():
    record = SessionRecord.from_dict(_record())
    assert record.persona == DEFAULT_PERSONA.key


def test_session_record_rejects_a_non_string_persona():
    with pytest.raises(ValueError):
        SessionRecord.from_dict(_record(persona=5))


def test_rename_session_changes_only_the_name(tmp_path):
    store = SessionStore(tmp_path / "store.json")
    asyncio.run(store.load())
    asyncio.run(store.add_session(1, SessionRecord.from_dict(_record())))
    asyncio.run(store.rename_session(1, "aaaaaaaa", "Новое имя (Писатель)"))
    session = store.find_session(1, "aaaaaaaa")
    assert session.name == "Новое имя (Писатель)"
    assert session.session_id == "sess_00000001"


def test_rename_of_an_unknown_session_is_refused(tmp_path):
    store = SessionStore(tmp_path / "store.json")
    asyncio.run(store.load())
    with pytest.raises(StoreError):
        asyncio.run(store.rename_session(1, "missing0", "x"))


def test_a_character_overrides_a_phrase_and_the_base_keeps_its_default():
    class Quiet(Persona):
        key = "quiet"
        no_active = "Тишина."

    assert Quiet().no_active == "Тишина."
    assert Persona().no_active.startswith("Беседа не открыта")


def test_persona_for_returns_the_character_of_the_conversation(tmp_path):
    store = SessionStore(tmp_path / "store.json")
    asyncio.run(store.load())
    asyncio.run(store.add_session(1, SessionRecord.from_dict(_record(persona="writer"))))
    asyncio.run(store.set_active(1, "aaaaaaaa"))
    services = Services(store=store, gate=ContextGate(store), dispatcher=object())
    assert persona_for(services, 1) is PERSONAS["writer"]
    assert persona_for(services, 2) is DEFAULT_PERSONA  # no conversation: the default character
