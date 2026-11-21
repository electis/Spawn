"""Config, credentials, KIRO_API_KEY loading, and Property 20 (secrets never in output)."""

import logging
import os
from pathlib import Path

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from bot.config import ConfigError, parse_config
from bot.credentials import CredentialError, load_token
from bot.kiro_env import KiroEnvError, load_kiro_env
from bot.redact import MASK, SecretRedactor

CONFIG_OK = """
configured_group_ids = [-1001234567890]
store_path = "/var/lib/kiro-bot/session_store.json"
workdir_root = "/var/lib/kiro-bot/workdirs"
"""


def test_config_defaults():
    cfg = parse_config(CONFIG_OK)
    assert cfg.configured_group_ids == (-1001234567890,)
    assert cfg.membership_ttl_seconds == 300
    assert cfg.kiro_bin == "/root/.local/bin/kiro-cli"


def test_missing_group_list_is_an_error():
    with pytest.raises(ConfigError):
        parse_config('store_path = "x"\nworkdir_root = "y"\n')


def test_empty_group_list_is_allowed():
    cfg = parse_config(CONFIG_OK.replace("[-1001234567890]", "[]"))
    assert cfg.configured_group_ids == ()


def test_token_env_takes_precedence(tmp_path):
    token_file = tmp_path / "telegram.env"
    token_file.write_text("TELEGRAM_BOT_TOKEN=from-file\n", encoding="utf-8")
    if os.name == "posix":
        token_file.chmod(0o600)
    assert load_token({"TELEGRAM_BOT_TOKEN": "from-env"}, token_file) == "from-env"


def test_token_from_file(tmp_path):
    if os.name != "posix":
        pytest.skip("mode check is POSIX only")
    token_file = tmp_path / "telegram.env"
    token_file.write_text("TELEGRAM_BOT_TOKEN='abc:123'\n", encoding="utf-8")
    token_file.chmod(0o600)
    assert load_token({}, token_file) == "abc:123"


def test_group_readable_token_file_is_refused(tmp_path):
    if os.name != "posix":
        pytest.skip("mode check is POSIX only")
    token_file = tmp_path / "telegram.env"
    token_file.write_text("TELEGRAM_BOT_TOKEN=abc\n", encoding="utf-8")
    token_file.chmod(0o640)
    with pytest.raises(CredentialError) as info:
        load_token({}, token_file)
    assert "abc" not in str(info.value)


def test_missing_token_file_is_an_error(tmp_path):
    with pytest.raises(CredentialError):
        load_token({}, tmp_path / "missing.env")


def _private_file(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
    return path


def test_kiro_env_builds_child_env(tmp_path):
    env_file = _private_file(tmp_path / ".env", "KIRO_API_KEY=secret-value\n")
    env = load_kiro_env(env_file, base_env={"PATH": "/usr/bin"})
    assert env["KIRO_API_KEY"] == "secret-value"
    assert env["PATH"] == "/usr/bin"


def test_kiro_env_missing_key_error_has_no_value(tmp_path):
    env_file = _private_file(tmp_path / ".env", "OTHER=x\n")
    with pytest.raises(KiroEnvError) as info:
        load_kiro_env(env_file, base_env={})
    assert "x" not in str(info.value).split("missing")[0]


def test_kiro_env_refuses_group_readable_file(tmp_path):
    if os.name != "posix":
        pytest.skip("mode check is POSIX only")
    env_file = tmp_path / ".env"
    env_file.write_text("KIRO_API_KEY=secret-value\n", encoding="utf-8")
    env_file.chmod(0o644)
    with pytest.raises(KiroEnvError) as info:
        load_kiro_env(env_file, base_env={})
    assert "secret-value" not in str(info.value)


# Feature: telegram-kiro-bot, Property 20: Secrets never appear in output
@settings(max_examples=200)
@given(
    secret=st.text(alphabet=st.characters(min_codepoint=33, max_codepoint=126), min_size=6, max_size=40),
    prefix=st.text(max_size=30),
    suffix=st.text(max_size=30),
)
def test_secret_never_in_formatted_record(secret, prefix, suffix):
    redactor = SecretRedactor([secret])
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1, "%s%s%s", (prefix, secret, suffix), None
    )
    assert redactor.filter(record)
    out = record.getMessage()
    assert secret not in out
    assert MASK in out


def test_redactor_masks_exception_text():
    redactor = SecretRedactor(["hunter2secret"])
    try:
        raise RuntimeError("bad key hunter2secret")
    except RuntimeError:
        import sys

        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    redactor.filter(record)
    assert "hunter2secret" not in logging.Formatter().format(record)
