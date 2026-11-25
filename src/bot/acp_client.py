"""Persistent connection to `kiro-cli acp` (Agent Client Protocol over stdio).

One connection is one kiro-cli process for one working directory. The process stays alive
between messages, so only its first start pays the start-up cost. Prompts on one connection run
one at a time (the connection lock). Permission requests from the agent are refused, so no tool
runs without approval.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from bot.kiro_parser import credits_from_meta, usage_from_meta

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
STREAM_LIMIT = 16 * 1024 * 1024  # one JSON line can carry a long reply
INIT_TIMEOUT_SECONDS = 120.0  # the first start of kiro-cli took about 10 seconds
REQUEST_TIMEOUT_SECONDS = 60.0
METHOD_NOT_FOUND = -32601


class AcpError(Exception):
    """The connection failed, or the agent returned an error. Messages never hold secrets."""


@dataclass
class PromptResult:
    stop_reason: str | None
    text: str
    usage: float | None  # context window fill, 0..1
    credits: float | None = None  # credits billed for this turn


@dataclass
class _Collector:
    """Gathers one prompt's reply text and context usage from session/update notifications."""

    chunks: list[str] = field(default_factory=list)
    primary: float | None = None
    fallback: float | None = None
    credits: float | None = None
    on_text: Callable[[str], None] | None = None  # called with each reply chunk as it arrives

    def feed(self, update: Mapping[str, Any]) -> None:
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            content = update.get("content")
            if isinstance(content, Mapping) and isinstance(content.get("text"), str):
                self.chunks.append(content["text"])
                if self.on_text is not None:
                    self.on_text(content["text"])
        elif kind == "session_info_update":
            primary, fallback = usage_from_meta(update.get("_meta"))
            if primary is not None:
                self.primary = primary
            if fallback is not None:
                self.fallback = fallback
            turn_credits = credits_from_meta(update.get("_meta"))
            if turn_credits is not None:
                self.credits = turn_credits

    @property
    def usage(self) -> float | None:
        return self.primary if self.primary is not None else self.fallback


def _model_values(config_options: Any) -> list[str]:
    """Return the model ids offered in session/new configOptions (option id "model")."""
    if not isinstance(config_options, list):
        return []
    for option in config_options:
        if isinstance(option, dict) and option.get("id") == "model":
            values = option.get("options")
            if isinstance(values, list):
                return [v["value"] for v in values if isinstance(v, dict) and isinstance(v.get("value"), str)]
    return []


class AcpConnection:
    def __init__(self, argv: Sequence[str], env: Mapping[str, str], cwd: str) -> None:
        self._argv = list(argv)
        self._env = dict(env)
        self._cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 0
        self._collector: _Collector | None = None
        self._closed = False
        self._dead = False  # set when kiro-cli stops answering, even before its exit code is known
        self.lock = asyncio.Lock()
        self.loaded: set[str] = set()
        self.model_options: list[str] = []  # model ids reported by the last session/new
        self.last_used = time.monotonic()

    @property
    def alive(self) -> bool:
        return (
            not self._closed
            and not self._dead
            and self._proc is not None
            and self._proc.returncode is None
        )

    @property
    def busy(self) -> bool:
        return self.lock.locked()

    async def start(self) -> None:
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._argv,
                cwd=self._cwd,
                env=self._env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,  # stderr may hold secrets from the environment
                limit=STREAM_LIMIT,
            )
        except OSError as exc:
            raise AcpError("could not start kiro-cli") from exc
        self._reader = asyncio.create_task(self._read_loop())
        await self._request(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "clientCapabilities": {}},
            INIT_TIMEOUT_SECONDS,
        )

    async def new_session(self, cwd: str) -> str:
        result = await self._request(
            "session/new", {"cwd": cwd, "mcpServers": []}, REQUEST_TIMEOUT_SECONDS
        )
        session_id = result.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise AcpError("kiro-cli returned no session id")
        self.loaded.add(session_id)
        self.model_options = _model_values(result.get("configOptions"))
        return session_id

    async def list_session_ids(self, cwd: str) -> set[str]:
        """Ids of the stored sessions in cwd, across all pages of session/list."""
        ids: set[str] = set()
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"cwd": cwd}
            if cursor:
                params["cursor"] = cursor
            result = await self._request("session/list", params, REQUEST_TIMEOUT_SECONDS)
            for item in result.get("sessions") or []:
                if isinstance(item, dict) and isinstance(item.get("sessionId"), str):
                    ids.add(item["sessionId"])
            cursor = result.get("nextCursor")
            if not isinstance(cursor, str) or not cursor:
                return ids

    async def load_session(self, session_id: str, cwd: str) -> None:
        await self._request(
            "session/load",
            {"sessionId": session_id, "cwd": cwd, "mcpServers": []},
            REQUEST_TIMEOUT_SECONDS,
        )
        self.loaded.add(session_id)

    async def delete_session(self, session_id: str) -> None:
        await self._request(
            "session/delete", {"sessionId": session_id}, REQUEST_TIMEOUT_SECONDS
        )
        self.loaded.discard(session_id)

    async def set_config_option(self, session_id: str, config_id: str, value: str) -> None:
        """Change one session option, for example the model (`configId="model"`)."""
        await self._request(
            "session/set_config_option",
            {"sessionId": session_id, "configId": config_id, "value": value},
            REQUEST_TIMEOUT_SECONDS,
        )

    async def prompt(
        self,
        session_id: str,
        text: str,
        timeout: float,
        on_text: Callable[[str], None] | None = None,
    ) -> PromptResult:
        """Send one prompt and wait for its end. A timed-out connection is closed."""
        self._collector = _Collector(on_text=on_text)
        try:
            try:
                response = await self._request(
                    "session/prompt",
                    {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]},
                    timeout,
                )
            except asyncio.TimeoutError:
                await self.close()
                raise AcpError("kiro-cli timed out") from None
            collector = self._collector
        finally:
            self._collector = None
        assert collector is not None
        stop_reason = response.get("stopReason")
        return PromptResult(
            stop_reason=stop_reason if isinstance(stop_reason, str) else None,
            text="".join(collector.chunks),
            usage=collector.usage,
            credits=collector.credits,
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._proc is not None:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        if self._reader is not None:
            self._reader.cancel()
        self._fail_pending(AcpError("kiro-cli connection closed"))

    async def _request(
        self, method: str, params: Mapping[str, Any], timeout: float
    ) -> dict[str, Any]:
        if self._closed or self._proc is None or self._proc.stdin is None:
            raise AcpError("kiro-cli connection is not open")
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            message = await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(request_id, None)
        if "error" in message:
            raise AcpError(f"kiro-cli returned an error for {method}")
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    async def _write(self, message: Mapping[str, Any]) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        try:
            self._proc.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise AcpError("kiro-cli connection is broken") from exc

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                try:
                    message = json.loads(line)
                except ValueError:
                    continue  # only JSON-RPC messages count
                if isinstance(message, dict):
                    self._dispatch(message)
        except ValueError:
            logger.error("kiro-cli sent a line larger than the stream limit")
        finally:
            self._dead = True
            self._fail_pending(AcpError("kiro-cli closed the connection"))

    def _dispatch(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if method is not None and "id" in message:
            # A request from the agent. It cannot be answered by us, so it is refused.
            asyncio.get_running_loop().create_task(self._refuse(message))
            return
        if method == "session/update":
            params = message.get("params")
            update = params.get("update") if isinstance(params, dict) else None
            if self._collector is not None and isinstance(update, dict):
                self._collector.feed(update)
            return
        request_id = message.get("id")
        if isinstance(request_id, int) and ("result" in message or "error" in message):
            future = self._pending.get(request_id)
            if future is not None and not future.done():
                future.set_result(message)

    async def _refuse(self, request: dict[str, Any]) -> None:
        if request.get("method") == "session/request_permission":
            reply: dict[str, Any] = {"outcome": {"outcome": "cancelled"}}
            body = {"jsonrpc": "2.0", "id": request["id"], "result": reply}
        else:
            body = {
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {"code": METHOD_NOT_FOUND, "message": "not supported by this client"},
            }
        try:
            await self._write(body)
        except AcpError:
            logger.debug("could not answer an agent request: connection broken")

    def _fail_pending(self, error: AcpError) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
