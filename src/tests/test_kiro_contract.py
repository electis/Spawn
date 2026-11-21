"""kiro-cli contract tests: parser shapes (Property 5), argv (Property 7), stdin only (Property 4).

Shapes are the ones recorded from kiro-cli 3.5.0 on amster.
"""

import asyncio
import json
from pathlib import Path

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from bot.kiro_parser import parse_stream
from bot.kiro_runner import KiroRunner, build_argv
from tests.fake_kiro import fake_env, make_fake_kiro

SESSION = "sess_3a313626-195f-4084-b687-bfa7b59d072a"

SESSION_UPDATE_PRIMARY = {
    "type": "sessionUpdate",
    "data": {
        "sessionId": SESSION,
        "update": {
            "sessionUpdate": "session_info_update",
            "_meta": {"kiro": {"kind": "context_usage", "usagePercentage": 1.25}},
        },
    },
}
SESSION_UPDATE_FALLBACK = {
    "type": "sessionUpdate",
    "data": {
        "sessionId": SESSION,
        "update": {
            "sessionUpdate": "session_info_update",
            "_meta": {"kiro": {"contextUsage": {"usagePercentage": 0.7859}}},
        },
    },
}
RUN_FINISHED = {
    "type": "runFinished",
    "data": {"sessionId": SESSION, "status": "success", "finalText": "OK"},
}


def _lines(*events):
    return [json.dumps(e) for e in events]


def test_primary_usage_wins_over_fallback():
    result = parse_stream(_lines(SESSION_UPDATE_FALLBACK, SESSION_UPDATE_PRIMARY, RUN_FINISHED))
    assert result.context_usage == 1.25
    assert result.session_id == SESSION
    assert result.final_text == "OK"
    assert result.error is None


def test_fallback_used_when_primary_absent():
    result = parse_stream(_lines(SESSION_UPDATE_FALLBACK, RUN_FINISHED))
    assert result.context_usage == pytest.approx(0.7859)


def test_no_usage_gives_none():
    result = parse_stream(_lines(RUN_FINISHED))
    assert result.context_usage is None and result.final_text == "OK"


def test_missing_final_text_is_an_error():
    event = {"type": "runFinished", "data": {"sessionId": SESSION, "status": "success"}}
    result = parse_stream(_lines(event))
    assert result.final_text is None and result.error


def test_run_error_is_reported():
    event = {"type": "runError", "data": {"message": "boom"}}
    result = parse_stream(_lines(event))
    assert result.error == "boom" and result.final_text is None


def test_non_json_lines_are_skipped():
    result = parse_stream(["noise", ""] + _lines(RUN_FINISHED))
    assert result.final_text == "OK"


def test_boolean_usage_is_ignored():
    event = {
        "type": "sessionUpdate",
        "data": {
            "sessionId": SESSION,
            "update": {"_meta": {"kiro": {"kind": "context_usage", "usagePercentage": True}}},
        },
    }
    assert parse_stream(_lines(event, RUN_FINISHED)).context_usage is None


def test_argv_without_session():
    argv = build_argv(None)
    assert "--resume-id" not in argv
    assert argv[:3] == ["chat", "--v3", "--no-interactive"]
    assert "claude-haiku-5.5" in argv


def test_argv_trusts_no_tools():
    argv = build_argv(None)
    assert "--trust-tools=" in argv
    assert not any(a.startswith("--trust-all") or a == "-a" for a in argv)


def test_argv_with_session():
    argv = build_argv(SESSION)
    assert argv[-2:] == ["--resume-id", SESSION]


@pytest.mark.parametrize("bad", ["sess_;rm", "../x", "--help", "", "SESS_UPPER"])
def test_invalid_session_id_is_rejected(bad):
    with pytest.raises(ValueError):
        build_argv(bad)


def _run(tmp_path: Path, text: str, session_id=None, **extra_env):
    fake = make_fake_kiro(tmp_path)
    env = fake_env(tmp_path, out_text="\n".join(_lines(RUN_FINISHED)) + "\n", **extra_env)
    runner = KiroRunner(str(fake), env, tmp_path / "workdirs", timeout=10)
    workdir = runner.workdir_for(7)
    result = asyncio.run(runner.run_prompt(workdir, session_id, text))
    log = json.loads(Path(env["FAKE_LOG"]).read_text(encoding="utf-8"))
    return result, log


# Feature: telegram-kiro-bot, Property 4: User text reaches kiro-cli only through stdin
@settings(max_examples=25, deadline=None)
@given(
    text=st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=1, max_size=80)
)
def test_text_goes_only_through_stdin(tmp_path_factory, text):
    tmp_path = tmp_path_factory.mktemp("run")
    result, log = _run(tmp_path, text)
    assert result.final_text == "OK"
    assert log["stdin"] == text
    assert text not in log["argv"]  # the text itself is never an argument


# Feature: telegram-kiro-bot, Property 7: Resume argument matches the stored Session_ID
def test_resume_argument_is_passed_for_stored_session(tmp_path):
    _, log = _run(tmp_path, "hi", session_id=SESSION)
    assert log["argv"][-2:] == ["--resume-id", SESSION]


def test_new_session_has_no_resume_argument(tmp_path):
    _, log = _run(tmp_path, "hi", session_id=None)
    assert "--resume-id" not in log["argv"]


def test_workdir_is_used_as_cwd(tmp_path):
    _, log = _run(tmp_path, "hi")
    assert Path(log["cwd"]).name == "7"


def test_non_zero_exit_is_an_error_without_stderr(tmp_path):
    result, _ = _run(tmp_path, "hi", FAKE_RC="3")
    assert result.final_text is None
    assert result.error == "kiro-cli exited with code 3"


def test_timeout_kills_and_reports(tmp_path):
    fake = make_fake_kiro(tmp_path)
    env = fake_env(tmp_path, out_text="", FAKE_SLEEP="5")
    runner = KiroRunner(str(fake), env, tmp_path / "workdirs", timeout=0.5)
    result = asyncio.run(runner.run_prompt(runner.workdir_for(1), None, "hi"))
    assert result.error == "kiro-cli timed out"


def test_delete_success_and_failure(tmp_path):
    fake = make_fake_kiro(tmp_path)
    env = fake_env(tmp_path)
    runner = KiroRunner(str(fake), env, tmp_path / "workdirs", timeout=10)
    workdir = runner.workdir_for(1)
    assert asyncio.run(runner.delete_session(workdir, SESSION)) is None

    env_bad = fake_env(tmp_path, FAKE_DELETE_RC="4")
    bad = KiroRunner(str(fake), env_bad, tmp_path / "workdirs", timeout=10)
    assert asyncio.run(bad.delete_session(workdir, SESSION)) == "kiro-cli delete exited with code 4"


def test_delete_rejects_bad_session_id_without_running(tmp_path):
    fake = make_fake_kiro(tmp_path)
    runner = KiroRunner(str(fake), fake_env(tmp_path), tmp_path / "workdirs", timeout=10)
    error = asyncio.run(runner.delete_session(runner.workdir_for(1), "bad id"))
    assert error == "invalid session id"
    assert not (tmp_path / "log.json").exists()


REFUSAL_RUN = {
    "type": "runFinished",
    "data": {
        "sessionId": SESSION,
        "status": "error",
        "stopReason": "refusal",
        "finalText": "The selected model cannot continue this conversation.",
    },
}


def test_refusal_is_reported_with_reason_and_session():
    result = parse_stream(_lines(REFUSAL_RUN))
    assert result.final_text is None
    assert result.session_id == SESSION
    assert "отказалась" in result.error
    assert "cannot continue" in result.error


def test_refusal_exit_code_keeps_reason(tmp_path):
    out = "\n".join(_lines(REFUSAL_RUN)) + "\n"
    fake = make_fake_kiro(tmp_path)
    env = fake_env(tmp_path, out_text=out, FAKE_RC="1")
    runner = KiroRunner(str(fake), env, tmp_path / "workdirs", timeout=10)
    result = asyncio.run(runner.run_prompt(runner.workdir_for(3), None, "hi"))
    assert result.error is not None
    assert "отказалась" in result.error
    assert "exited with code" not in result.error


def test_exit_code_without_stream_reason_is_still_an_error(tmp_path):
    fake = make_fake_kiro(tmp_path)
    env = fake_env(tmp_path, out_text="", FAKE_RC="2")
    runner = KiroRunner(str(fake), env, tmp_path / "workdirs", timeout=10)
    result = asyncio.run(runner.run_prompt(runner.workdir_for(3), None, "hi"))
    assert result.error == "kiro-cli exited with code 2"

def test_refusal_sets_refused_flag():
    assert parse_stream(_lines(REFUSAL_RUN)).refused is True


def test_other_failures_are_not_refusals():
    assert parse_stream(_lines({"type": "runError", "data": {"message": "boom"}})).refused is False
    assert parse_stream(_lines(RUN_FINISHED)).refused is False