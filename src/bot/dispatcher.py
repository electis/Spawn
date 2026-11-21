"""Per-User job queue (Requirements 8.1 to 8.3, 10.15).

One asyncio worker and one FIFO queue per User. Workers for different Users run in parallel.
A job is removed from the User's queue only after it finishes, so jobs for one User never overlap.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TextJob:
    user_id: int
    sid: str  # Session bound at submit time; may be a pending (not yet stored) Session
    text: str
    pending_name: str | None = None  # set when the Session is new and not yet stored
    summary_of: str | None = None  # old Session sid when this new Session comes from a summary


@dataclass(frozen=True)
class SummaryJob:
    user_id: int
    sid: str  # the Active_Session at tap time


@dataclass(frozen=True)
class DeleteJob:
    user_id: int
    sid: str


Job = TextJob | SummaryJob | DeleteJob
JobHandler = Callable[[Job], Awaitable[None]]
BlockCheck = Callable[[TextJob], bool]
BlockedHandler = Callable[[TextJob], Awaitable[None]]
TypingHandler = Callable[[int], Awaitable[None]]  # sends a "typing" chat action to a user


class UserDispatcher:
    def __init__(
        self,
        handle: JobHandler,
        is_blocked: BlockCheck | None = None,
        on_blocked: BlockedHandler | None = None,
        show_typing: TypingHandler | None = None,
        typing_interval: float = 4.0,
    ) -> None:
        self._handle = handle
        self._is_blocked = is_blocked
        self._on_blocked = on_blocked
        self._show_typing = show_typing
        self._typing_interval = typing_interval
        self._queues: dict[int, asyncio.Queue[Job]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def has_worker(self, user_id: int) -> bool:
        return user_id in self._queues

    async def submit(self, job: Job) -> None:
        queue = self._queues.get(job.user_id)
        if queue is None:
            queue = asyncio.Queue()
            self._queues[job.user_id] = queue
            task = asyncio.create_task(self._worker(job.user_id, queue))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        await queue.put(job)

    async def drain(self) -> None:
        """Wait until every queued job has finished. Used by tests and shutdown."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _run_with_typing(self, job: Job) -> None:
        """Run a job while the chat shows "typing" (text, summary, and delete)."""
        if self._show_typing is None:
            await self._handle(job)
            return
        typing = asyncio.create_task(self._typing_loop(job.user_id))
        try:
            await self._handle(job)
        finally:
            typing.cancel()
            with suppress(asyncio.CancelledError):
                await typing

    async def _typing_loop(self, user_id: int) -> None:
        # Telegram shows a typing action for about 5 seconds, so it is refreshed every 4.
        assert self._show_typing is not None
        while True:
            try:
                await self._show_typing(user_id)
            except Exception as exc:  # noqa: BLE001 - a missing indicator must not fail the job
                logger.debug("typing action failed: %s", type(exc).__name__)
            await asyncio.sleep(self._typing_interval)

    async def _worker(self, user_id: int, queue: asyncio.Queue[Job]) -> None:
        while True:
            job = await queue.get()
            try:
                if isinstance(job, TextJob) and self._is_blocked and self._is_blocked(job):
                    # A full Session discards the queued text (Assumption A-11).
                    if self._on_blocked is not None:
                        await self._on_blocked(job)
                else:
                    await self._run_with_typing(job)
            except Exception as exc:  # noqa: BLE001 - one failed job must not stop the worker
                logger.error("job %s failed: %s", type(job).__name__, type(exc).__name__)
            finally:
                queue.task_done()
            if queue.empty():
                # No await between this check and the removal, so no job can slip in.
                del self._queues[user_id]
                return
