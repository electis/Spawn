"""Job execution: text replies, summaries, and deletes (Requirements 2.3 to 2.5, 3.2, 4.5,
5.7, 5.8, 7.3 to 7.6, 10.2 to 10.13).

The Session_Store changes only on the success paths described in the design.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from bot.context_display import Display, display_for
from bot.dispatcher import DeleteJob, Job, SummaryJob, TextJob
from bot.kiro_parser import RunResult
from bot.models import SessionRecord
from bot.session_store import SessionStore
from bot.splitter import split_reply
from bot.summary import SUMMARY_PROMPT

ERROR_TEXT = "Не удалось получить ответ от kiro-cli. Сессия не изменена."
NEW_SESSION_FAILED_TEXT = "Новая сессия не создана. Текущая сессия остаётся активной."
SUMMARY_FAILED_TEXT = "Не удалось сделать выжимку. Текущая сессия не изменена."
DELETE_FAILED_TEXT = "Не удалось удалить сессию. Она осталась в списке."
DELETED_TEXT = "Сессия удалена. Откройте или создайте сессию, чтобы продолжить."
NO_SESSION_TEXT = "Сессия не найдена. Откройте или создайте сессию."


class Notifier(Protocol):
    async def send_text(self, user_id: int, text: str) -> None: ...

    async def send_reply(
        self,
        user_id: int,
        chunks: list[str],
        display: Display,
        choice: tuple[str, str] | None,
    ) -> None:
        """choice is (new_sid, old_sid) after a summary, otherwise None."""

    async def send_summary(self, user_id: int, text: str, old_sid: str) -> None: ...

    async def send_full_options(self, user_id: int) -> None: ...


class Runner(Protocol):
    def workdir_for(self, user_id: int) -> Path: ...

    async def run_prompt(self, workdir: Path, session_id: str | None, text: str) -> RunResult: ...

    async def delete_session(self, workdir: Path, session_id: str) -> str | None: ...


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class JobProcessor:
    store: SessionStore
    runner: Runner
    notifier: Notifier
    now: Callable[[], str] = utc_now_iso

    async def handle(self, job: Job) -> None:
        if isinstance(job, TextJob):
            await self.text(job)
        elif isinstance(job, SummaryJob):
            await self.summary(job)
        elif isinstance(job, DeleteJob):
            await self.delete(job)

    async def text(self, job: TextJob) -> None:
        session = self.store.find_session(job.user_id, job.sid)
        if session is None and job.pending_name is None:
            await self.notifier.send_text(job.user_id, NO_SESSION_TEXT)
            return

        resume_id = session.session_id if session is not None else None
        workdir = self.runner.workdir_for(job.user_id)
        result = await self.runner.run_prompt(workdir, resume_id, job.text)

        if result.error is not None or result.final_text is None:
            await self.notifier.send_text(
                job.user_id,
                NEW_SESSION_FAILED_TEXT if session is None and job.pending_name else ERROR_TEXT,
            )
            return

        when = self.now()
        choice: tuple[str, str] | None = None
        if session is None:
            # First successful reply of a new Session: store it now (Requirement 5.7).
            if not result.session_id:
                await self.notifier.send_text(job.user_id, NEW_SESSION_FAILED_TEXT)
                return
            record = SessionRecord(
                sid=job.sid,
                name=job.pending_name or job.sid,
                session_id=result.session_id,
                context_usage=result.context_usage,
                created_at=when,
                updated_at=when,
            )
            await self.store.add_session(job.user_id, record)
            await self.store.set_active(job.user_id, job.sid)
            stored_usage = result.context_usage
            if job.summary_of is not None:
                choice = (job.sid, job.summary_of)  # Requirement 10.8
        else:
            # A reply with no usage value keeps the stored value (Requirement 4.5).
            if result.context_usage is not None:
                await self.store.set_context_usage(job.user_id, job.sid, result.context_usage)
            await self.store.touch_session(job.user_id, job.sid, when)
            stored_usage = (
                result.context_usage if result.context_usage is not None else session.context_usage
            )

        await self.notifier.send_reply(
            job.user_id, split_reply(result.final_text), display_for(stored_usage), choice
        )

    async def summary(self, job: SummaryJob) -> None:
        session = self.store.find_session(job.user_id, job.sid)
        if session is None or not session.session_id:
            await self.notifier.send_text(job.user_id, NO_SESSION_TEXT)
            return

        # The Summary_Prompt goes to the old Session. No new Session is created here (10.3).
        workdir = self.runner.workdir_for(job.user_id)
        result = await self.runner.run_prompt(workdir, session.session_id, SUMMARY_PROMPT)
        if result.error is not None or result.final_text is None:
            await self.notifier.send_text(job.user_id, SUMMARY_FAILED_TEXT)
            return

        if result.context_usage is not None:
            await self.store.set_context_usage(job.user_id, job.sid, result.context_usage)
        await self.notifier.send_summary(job.user_id, result.final_text, job.sid)

    async def delete(self, job: DeleteJob) -> None:
        session = self.store.find_session(job.user_id, job.sid)
        if session is None:
            await self.notifier.send_text(job.user_id, NO_SESSION_TEXT)
            return
        if session.session_id:
            workdir = self.runner.workdir_for(job.user_id)
            error = await self.runner.delete_session(workdir, session.session_id)
            if error is not None:
                await self.notifier.send_text(job.user_id, DELETE_FAILED_TEXT)
                return
        await self.store.remove_session(job.user_id, job.sid)
        await self.notifier.send_text(job.user_id, DELETED_TEXT)
