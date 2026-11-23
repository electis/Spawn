"""Runs kiro-cli prompts over ACP (design: KiroRunner, Security).

One persistent `kiro-cli acp` process serves each working directory (one User or group member).
A stored Session is reopened with session/load, a new one is created with session/new.
kiro-cli runs under its own HOME (acp_home), where chat.defaultModel selects the model for these
processes only. The chat CLI and the rest of the server keep their own configuration.
The error messages never include stderr or the environment, because either may hold a secret.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from bot.acp_client import AcpConnection, AcpError
from bot.kiro_parser import NO_FINAL_TEXT, RunResult

logger = logging.getLogger(__name__)

ACP_ARGS: tuple[str, ...] = ("acp", "--agent-engine", "v3")
SESSION_ID_RE = re.compile(r"^(sess_[0-9a-f-]+|[0-9a-f-]{36})$")
REFUSAL_MESSAGE = "модель отказалась ответить на этот запрос"
DEFAULT_TIMEOUT_SECONDS = 600.0
IDLE_SECONDS = 900.0  # a process idle this long is closed
MAX_CONNECTIONS = 8  # least recently used idle process is closed above this


class KiroRunner:
    def __init__(
        self,
        kiro_bin: str,
        env: Mapping[str, str],
        workdir_root: Path,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        acp_home: Path | None = None,
        acp_args: Sequence[str] = ACP_ARGS,
        idle_seconds: float = IDLE_SECONDS,
        max_connections: int = MAX_CONNECTIONS,
    ) -> None:
        self._argv = [kiro_bin, *acp_args]
        self._env = dict(env)
        if acp_home is not None:
            self._env["HOME"] = str(acp_home)
        self._root = Path(workdir_root)
        self._timeout = timeout
        self._idle = idle_seconds
        self._max = max_connections
        self._connections: dict[str, AcpConnection] = {}

    def workdir_for(self, user_id: int) -> Path:
        """Working_Directory of a User, created on first use (Requirement 3.4)."""
        path = self._root / str(user_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def run_prompt(self, workdir: Path, session_id: str | None, text: str) -> RunResult:
        if session_id is not None and not SESSION_ID_RE.fullmatch(session_id):
            return RunResult(None, None, None, "invalid session id")
        cwd = str(workdir)
        result_session = session_id
        try:
            conn = await self._connection(cwd)
            async with conn.lock:
                if session_id is None:
                    result_session = await conn.new_session(cwd)
                elif session_id not in conn.loaded:
                    await conn.load_session(session_id, cwd)
                result = await conn.prompt(result_session, text, self._timeout)
        except (AcpError, asyncio.TimeoutError) as exc:
            message = str(exc) if isinstance(exc, AcpError) else "kiro-cli did not answer in time"
            logger.error("kiro-cli request failed: %s", message)
            return RunResult(result_session, None, None, message)
        if result.stop_reason == "refusal":
            return RunResult(result_session, None, result.usage, REFUSAL_MESSAGE, refused=True)
        if not result.text:
            return RunResult(result_session, None, result.usage, NO_FINAL_TEXT)
        return RunResult(result_session, result.text, result.usage, None)

    async def delete_session(self, workdir: Path, session_id: str) -> str | None:
        """Delete a stored Session. None on success, otherwise an error text without secrets."""
        if not SESSION_ID_RE.fullmatch(session_id):
            return "invalid session id"
        cwd = str(workdir)
        try:
            conn = await self._connection(cwd)
            async with conn.lock:
                await conn.delete_session(session_id)
        except (AcpError, asyncio.TimeoutError) as exc:
            return str(exc) if isinstance(exc, AcpError) else "delete timed out"
        return None

    async def close(self) -> None:
        """Close every kiro-cli process. Called when the bot stops."""
        for conn in list(self._connections.values()):
            await conn.close()
        self._connections.clear()

    async def _connection(self, cwd: str) -> AcpConnection:
        await self._reap()
        conn = self._connections.get(cwd)
        if conn is None or not conn.alive:
            if conn is not None:
                await conn.close()
            conn = AcpConnection(self._argv, self._env, cwd)
            await conn.start()
            self._connections[cwd] = conn
        conn.last_used = time.monotonic()
        return conn

    async def _reap(self) -> None:
        """Close idle processes that are too old, and keep the number of processes bounded."""
        now = time.monotonic()
        for key, conn in list(self._connections.items()):
            if not conn.busy and (now - conn.last_used > self._idle or not conn.alive):
                await conn.close()
                del self._connections[key]
        if len(self._connections) >= self._max:
            idle = sorted(
                (c for c in self._connections.items() if not c[1].busy),
                key=lambda item: item[1].last_used,
            )
            for key, conn in idle[: len(self._connections) - self._max + 1]:
                await conn.close()
                del self._connections[key]
