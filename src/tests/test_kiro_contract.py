"""kiro-cli ACP contract tests, run against tests/fake_acp.py (no network, no real kiro-cli).

Each test runs its whole scenario inside one event loop, as the bot does: the kiro-cli process
and its reader task belong to that loop.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from bot.kiro_parser import NO_FINAL_TEXT, usage_from_meta
from bot.kiro_runner import REFUSAL_MESSAGE, KiroRunner
from tests.fake_acp import __file__ as FAKE_PATH

FAKE = str(Path(FAKE_PATH).resolve())


def _runner(tmp_path: Path, mode: str = "normal", acp_home=None, **kwargs) -> tuple[KiroRunner, Path]:
    log = tmp_path / "events.jsonl"
    env = {**os.environ, "FAKE_LOG": str(log), "FAKE_MODE": mode}
    runner = KiroRunner(
        sys.executable,
        env,
        tmp_path / "workdirs",
        acp_home=acp_home,
        acp_args=(FAKE,),
        **kwargs,
    )
    return runner, log


def _events(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def _requests(log: Path, method: str) -> list[dict]:
    return [e["params"] for e in _events(log) if e["event"] == "request" and e["method"] == method]


def _starts(log: Path) -> int:
    return len([e for e in _events(log) if e["event"] == "start"])


# ---- usage parsing --------------------------------------------------------------------


def test_primary_usage_shape():
    meta = {"kiro": {"kind": "context_usage", "usagePercentage": 1.25}}
    assert usage_from_meta(meta) == (1.25, None)


def test_fallback_usage_shape():
    meta = {"kiro": {"contextUsage": {"usagePercentage": 0.7859}}}
    assert usage_from_meta(meta) == (None, 0.7859)


@pytest.mark.parametrize("meta", [None, {}, {"kiro": "x"}, {"kiro": {"kind": "context_usage", "usagePercentage": True}}])
def test_unusable_usage_is_ignored(meta):
    assert usage_from_meta(meta)[0] is None


# ---- prompts --------------------------------------------------------------------------


def test_new_session_returns_id_text_and_usage(tmp_path):
    async def scenario():
        runner, _ = _runner(tmp_path)
        try:
            return await runner.run_prompt(runner.workdir_for(1), None, "привет")
        finally:
            await runner.close()

    result = asyncio.run(scenario())
    assert result.error is None
    assert result.session_id.startswith("sess_")
    assert result.final_text == "Ответ: привет"
    assert result.context_usage == 1.25
    assert result.refused is False


def test_text_is_sent_in_the_prompt(tmp_path):
    secret_like = "пароль: 12345 --trust-tools=*"

    async def scenario():
        runner, log = _runner(tmp_path)
        try:
            await runner.run_prompt(runner.workdir_for(1), None, secret_like)
        finally:
            await runner.close()
        return log

    log = asyncio.run(scenario())
    (prompt,) = _requests(log, "session/prompt")
    assert prompt["prompt"] == [{"type": "text", "text": secret_like}]


def test_working_directory_is_the_session_cwd(tmp_path):
    async def scenario():
        runner, log = _runner(tmp_path)
        workdir = runner.workdir_for(7)
        try:
            await runner.run_prompt(workdir, None, "hi")
        finally:
            await runner.close()
        return workdir, log

    workdir, log = asyncio.run(scenario())
    (new,) = _requests(log, "session/new")
    assert new["cwd"] == str(workdir)


def test_process_is_reused_between_messages(tmp_path):
    async def scenario():
        runner, log = _runner(tmp_path)
        workdir = runner.workdir_for(2)
        try:
            first = await runner.run_prompt(workdir, None, "один")
            second = await runner.run_prompt(workdir, first.session_id, "два")
        finally:
            await runner.close()
        return second, log

    second, log = asyncio.run(scenario())
    assert _starts(log) == 1
    assert second.final_text == "Ответ: два"
    assert _requests(log, "session/load") == []  # already open in this process


def test_stored_session_is_reopened_with_session_load(tmp_path):
    async def scenario():
        runner, log = _runner(tmp_path)
        workdir = runner.workdir_for(3)
        try:
            result = await runner.run_prompt(workdir, "sess_0000abcd", "вспомни")
        finally:
            await runner.close()
        return workdir, result, log

    workdir, result, log = asyncio.run(scenario())
    (load,) = _requests(log, "session/load")
    assert load["sessionId"] == "sess_0000abcd"
    assert load["cwd"] == str(workdir)
    assert result.session_id == "sess_0000abcd"
    assert result.final_text == "Ответ: вспомни"


def test_unknown_session_is_reported_not_silently_replaced(tmp_path):
    async def scenario():
        runner, _ = _runner(tmp_path)
        try:
            # The fake rejects ids that do not start with sess_, so session/load fails.
            return await runner.run_prompt(runner.workdir_for(4), "9f3c" + "0" * 36, "hi")
        finally:
            await runner.close()

    result = asyncio.run(scenario())
    assert result.final_text is None
    assert result.error


def test_invalid_session_id_is_rejected_before_any_process(tmp_path):
    async def scenario():
        runner, log = _runner(tmp_path)
        try:
            return await runner.run_prompt(runner.workdir_for(5), "rm -rf /", "hi"), log
        finally:
            await runner.close()

    result, log = asyncio.run(scenario())
    assert result.error == "invalid session id"
    assert _events(log) == []


def test_refusal_is_reported_with_reason(tmp_path):
    async def scenario():
        runner, _ = _runner(tmp_path, mode="refusal")
        try:
            return await runner.run_prompt(runner.workdir_for(6), None, "опасное")
        finally:
            await runner.close()

    result = asyncio.run(scenario())
    assert result.refused is True
    assert result.error == REFUSAL_MESSAGE
    assert result.final_text is None


def test_empty_reply_is_an_error(tmp_path):
    async def scenario():
        runner, _ = _runner(tmp_path, mode="empty")
        try:
            return await runner.run_prompt(runner.workdir_for(8), None, "тишина")
        finally:
            await runner.close()

    result = asyncio.run(scenario())
    assert result.error == NO_FINAL_TEXT
    assert result.refused is False


def test_timeout_is_reported_and_next_message_restarts_kiro(tmp_path):
    async def scenario():
        runner, log = _runner(tmp_path, mode="hang", timeout=0.5)
        workdir = runner.workdir_for(9)
        try:
            first = await runner.run_prompt(workdir, None, "долго")
            runner._env["FAKE_MODE"] = "normal"  # the next process answers normally
            second = await runner.run_prompt(workdir, None, "снова")
        finally:
            await runner.close()
        return first, second, log

    first, second, log = asyncio.run(scenario())
    assert first.error and ("timed out" in first.error or "did not answer" in first.error)
    assert second.error is None
    assert _starts(log) == 2


def test_crashed_process_is_an_error_and_is_restarted(tmp_path):
    async def scenario():
        runner, log = _runner(tmp_path, mode="crash_on_prompt")
        workdir = runner.workdir_for(10)
        try:
            first = await runner.run_prompt(workdir, None, "упаду")
            runner._env["FAKE_MODE"] = "normal"
            second = await runner.run_prompt(workdir, None, "ещё раз")
        finally:
            await runner.close()
        return first, second, log

    first, second, log = asyncio.run(scenario())
    assert first.error
    assert second.final_text == "Ответ: ещё раз"
    assert _starts(log) == 2


def test_permission_requests_are_refused(tmp_path):
    async def scenario():
        runner, log = _runner(tmp_path, mode="permission")
        try:
            return await runner.run_prompt(runner.workdir_for(11), None, "нужен инструмент"), log
        finally:
            await runner.close()

    result, log = asyncio.run(scenario())
    assert result.final_text == "Ответ: нужен инструмент"
    (reply,) = [e["reply"] for e in _events(log) if e["event"] == "permission_reply"]
    assert reply["result"] == {"outcome": {"outcome": "cancelled"}}


def test_kiro_runs_under_the_acp_home(tmp_path):
    home = tmp_path / "acp-home"

    async def scenario():
        runner, log = _runner(tmp_path, acp_home=home)
        try:
            await runner.run_prompt(runner.workdir_for(12), None, "hi")
        finally:
            await runner.close()
        return log

    log = asyncio.run(scenario())
    (start,) = [e for e in _events(log) if e["event"] == "start"]
    assert start["home"] == str(home)


def test_least_recently_used_idle_process_is_closed(tmp_path):
    async def scenario():
        runner, log = _runner(tmp_path, max_connections=1)
        try:
            await runner.run_prompt(runner.workdir_for(13), None, "a")
            await runner.run_prompt(runner.workdir_for(14), None, "b")
            await runner.run_prompt(runner.workdir_for(13), None, "c")
        finally:
            await runner.close()
        return log

    log = asyncio.run(scenario())
    assert _starts(log) == 3


# ---- deletion -------------------------------------------------------------------------


def test_delete_uses_session_delete(tmp_path):
    async def scenario():
        runner, log = _runner(tmp_path)
        try:
            error = await runner.delete_session(runner.workdir_for(15), "sess_0000beef")
        finally:
            await runner.close()
        return error, log

    error, log = asyncio.run(scenario())
    assert error is None
    (delete,) = _requests(log, "session/delete")
    assert delete["sessionId"] == "sess_0000beef"


def test_delete_rejects_bad_session_id(tmp_path):
    async def scenario():
        runner, _ = _runner(tmp_path)
        return await runner.delete_session(runner.workdir_for(16), "bad id")

    assert asyncio.run(scenario()) == "invalid session id"
