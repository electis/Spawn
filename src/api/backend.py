"""Runs one API request on a kiro-cli ACP process (bot.acp_client), with the model chosen per request.

Each request uses a session of its own, or continues a session named by conversation_id.
API sessions are separate from the Telegram bot's sessions. They live in the API working directory.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from bot.acp_client import AcpConnection, AcpError
from bot.kiro_parser import NO_FINAL_TEXT
from bot.kiro_runner import ACP_ARGS, REFUSAL_MESSAGE
from bot.persona import Persona

logger = logging.getLogger(__name__)

SESSION_ID_RE = re.compile(r"^(sess_[0-9a-f-]+|[0-9a-f-]{36})$")


class ApiError(Exception):
    """An error with the HTTP status to return. Messages never include secrets."""

    def __init__(self, status: int, message: str, error_type: str = "invalid_request_error") -> None:
        super().__init__(message)
        self.status = status
        self.error_type = error_type


@dataclass
class Completion:
    conversation_id: str
    text: str
    credits: float | None = None  # credits billed for this request
    context_usage: float | None = None  # context window fill after this request, 0..1


class ApiBackend:
    def __init__(
        self,
        kiro_bin: str,
        env: dict[str, str],
        acp_home: Path,
        workdir: Path,
        max_processes: int,
        timeout: float,
    ) -> None:
        self._argv = [kiro_bin, *ACP_ARGS]
        self._env = {**env, "HOME": str(acp_home)}
        self._cwd = str(workdir)
        self._timeout = timeout
        self._slots = asyncio.Semaphore(max_processes)
        self._idle: list[AcpConnection] = []
        self.models: list[str] = []

    async def start(self) -> None:
        """Start one process and read the model list from it. Fails when kiro-cli does not work."""
        conn = AcpConnection(self._argv, self._env, self._cwd)
        await conn.start()
        try:
            probe = await conn.new_session(self._cwd)
            self.models = list(conn.model_options)
            logger.info("kiro-cli started, %d models available", len(self.models))
            conn.loaded.discard(probe)
        finally:
            self._idle.append(conn)

    async def close(self) -> None:
        for conn in self._idle:
            await conn.close()
        self._idle.clear()

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[AcpConnection]:
        await self._slots.acquire()
        try:
            conn = await self._take_idle()
            try:
                yield conn
            finally:
                if conn.alive:
                    self._idle.append(conn)
                else:
                    await conn.close()
        finally:
            self._slots.release()

    async def _take_idle(self) -> AcpConnection:
        while self._idle:
            conn = self._idle.pop()
            if conn.alive:
                return conn
            await conn.close()
        conn = AcpConnection(self._argv, self._env, self._cwd)
        await conn.start()
        return conn

    async def complete(
        self,
        *,
        model: str,
        persona: Persona,
        conversation_id: str | None,
        prompt: str,
        on_text: Callable[[str], None] | None = None,
    ) -> Completion:
        if self.models and model not in self.models:
            raise ApiError(400, f"unknown model: {model}")
        if conversation_id is not None and not SESSION_ID_RE.fullmatch(conversation_id):
            raise ApiError(400, "invalid conversation_id")
        async with self._connection() as conn:
            cwd = self._cwd
            if conversation_id is None:
                session_id = await self._new_session(conn, cwd)
                text = persona.first_message(prompt)
            else:
                session_id = conversation_id
                text = prompt
                if session_id not in conn.loaded:
                    # kiro-cli creates an empty session on session/load for an unknown id,
                    # so the id must be checked against the stored sessions first.
                    try:
                        stored = await conn.list_session_ids(cwd)
                        if session_id not in stored:
                            raise ApiError(404, "conversation not found")
                        await conn.load_session(session_id, cwd)
                    except AcpError as exc:
                        raise ApiError(502, "kiro-cli failed", "api_error") from exc
            try:
                await conn.set_config_option(session_id, "model", model)
            except AcpError as exc:
                if not conn.alive:
                    raise ApiError(502, "kiro-cli failed", "api_error") from exc
                raise ApiError(400, f"model cannot be selected: {model}") from exc
            try:
                result = await conn.prompt(session_id, text, self._timeout, on_text=on_text)
            except AcpError as exc:
                raise ApiError(502, "kiro-cli failed", "api_error") from exc
        usage = {"credits": result.credits, "context_usage": result.usage}
        if result.stop_reason == "refusal":
            return Completion(session_id, REFUSAL_MESSAGE, **usage)
        if not result.text:
            return Completion(session_id, NO_FINAL_TEXT, **usage)
        return Completion(session_id, result.text, **usage)

    async def _new_session(self, conn: AcpConnection, cwd: str) -> str:
        try:
            return await conn.new_session(cwd)
        except AcpError as exc:
            raise ApiError(502, "kiro-cli failed", "api_error") from exc
