"""Headless kiro-cli runner (design: KiroRunner, Security).

The user text always goes through stdin, never through argv (Requirements 2.1, 2.2).
Error messages never include stderr or the environment, because either may hold a secret.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from pathlib import Path

from bot.kiro_parser import NO_FINAL_TEXT, RunResult, parse_stream

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-5.5"
SESSION_ID_RE = re.compile(r"^(sess_[0-9a-f-]+|[0-9a-f-]{36})$")
BASE_ARGV: tuple[str, ...] = (
    "chat",
    "--v3",
    "--no-interactive",
    "--model",
    MODEL,
    "--output-format",
    "stream-json",
    # Trust no tools: headless runs have no approval prompt, so every tool call is refused.
    "--trust-tools=",
)
DEFAULT_TIMEOUT_SECONDS = 600.0


def build_argv(session_id: str | None) -> list[str]:
    """Fixed argv. `--resume-id` is added only for a stored Session_ID (Property 7)."""
    argv = list(BASE_ARGV)
    if session_id is not None:
        if not SESSION_ID_RE.fullmatch(session_id):
            raise ValueError("invalid session id")
        argv += ["--resume-id", session_id]
    return argv


class KiroRunner:
    def __init__(
        self,
        kiro_bin: str,
        env: Mapping[str, str],
        workdir_root: Path,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._bin = kiro_bin
        self._env = dict(env)
        self._root = Path(workdir_root)
        self._timeout = timeout

    def workdir_for(self, user_id: int) -> Path:
        """Working_Directory of a User, created on first use (Requirement 3.4)."""
        path = self._root / str(user_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def run_prompt(self, workdir: Path, session_id: str | None, text: str) -> RunResult:
        argv = build_argv(session_id)
        try:
            proc = await asyncio.create_subprocess_exec(
                self._bin,
                *argv,
                cwd=str(workdir),
                env=self._env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError:
            logger.error("could not start kiro-cli")
            return RunResult(None, None, None, "could not start kiro-cli")

        try:
            stdout, _stderr = await asyncio.wait_for(
                proc.communicate(input=text.encode("utf-8")), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return RunResult(None, None, None, "kiro-cli timed out")

        # stderr is read but not reported: it may repeat secrets from the environment.
        # The stream is parsed even on a non-zero exit: a refusal exits 1 and its reason is in
        # runFinished.
        parsed = parse_stream(stdout.decode("utf-8", errors="replace").splitlines())
        if proc.returncode != 0 and parsed.error in (None, NO_FINAL_TEXT):
            return RunResult(parsed.session_id, None, parsed.context_usage, f"kiro-cli exited with code {proc.returncode}")
        return parsed

    async def delete_session(self, workdir: Path, session_id: str) -> str | None:
        """Delete a saved Session. None on success, otherwise an error text without secrets."""
        if not SESSION_ID_RE.fullmatch(session_id):
            return "invalid session id"
        try:
            proc = await asyncio.create_subprocess_exec(
                self._bin,
                "chat",
                "--delete-session",
                session_id,
                cwd=str(workdir),
                env=self._env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=self._timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return "delete timed out"
        except OSError:
            return "could not start kiro-cli"
        if proc.returncode != 0:
            return f"kiro-cli delete exited with code {proc.returncode}"
        return None
