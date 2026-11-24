"""Job execution: text replies, summaries, and deletes (Requirements 2.3 to 2.5, 3.2, 4.5,
5.7, 5.8, 7.3 to 7.6, 10.2 to 10.13).
The Session_Store changes only on the success paths described in the design.
Text jobs from a group are answered in that group; summaries and deletes are private only.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from bot.context_display import (
    GROUP_SHOW_THRESHOLD,
    SHOW_THRESHOLD,
    Display,
    display_for,
)
from bot.dispatcher import DeleteJob, Job, SummaryJob, TextJob
from bot.kiro_parser import RunResult
from bot.models import SessionRecord
from bot.names import clean_title, session_title, validate_session_name
from bot.persona import DEFAULT_PERSONA, Persona, resolve_persona
from bot.session_store import SessionStore
from bot.splitter import split_reply



class Notifier(Protocol):
    async def send_text(self, chat_id: int, text: str, reply_to: int | None = None) -> None: ...

    async def send_reply(
        self,
        chat_id: int,
        chunks: list[str],
        display: Display,
        choice: tuple[str, str] | None,
        reply_to: int | None = None,
        group: bool = False,
        choice_text: str = "",
    ) -> None:
        """choice is (new_sid, old_sid) after a summary, otherwise None. choice_text labels it."""

    async def send_summary(self, user_id: int, text: str, old_sid: str) -> None: ...

    async def send_blocked(self, job: TextJob) -> None: ...


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
        chat = job.target_chat
        reply_to = job.reply_to
        group = job.chat_id is not None
        session = self.store.find_session(job.user_id, job.sid)
        if session is None and job.pending_name is None:
            await self.notifier.send_text(chat, resolve_persona(job.persona_key).no_session_text, reply_to)
            return
        first_turn = session is None
        if session is None:
            session = await self._create_session(job, chat, reply_to)
            if session is None:
                return
        persona = resolve_persona(session.persona)
        workdir = self.runner.workdir_for(job.user_id)
        # The first message of a Session carries the character instructions; later ones do not.
        prompt = persona.first_message(job.text) if first_turn else job.text
        result = await self.runner.run_prompt(workdir, session.session_id, prompt)
        if result.error is not None or result.final_text is None:
            if result.refused:
                await self.notifier.send_text(chat, random.choice(persona.refusal_replies), reply_to)
                return
            base = persona.first_reply_failed_text if first_turn else persona.error_text
            reason = result.error or "пустой ответ"
            await self.notifier.send_text(chat, f"{base}\nПричина: {reason}", reply_to)
            return
        when = self.now()
        # A reply with no usage value keeps the stored value (Requirement 4.5).
        if result.context_usage is not None:
            await self.store.set_context_usage(job.user_id, job.sid, result.context_usage)
        await self.store.touch_session(job.user_id, job.sid, when)
        stored_usage = (
            result.context_usage if result.context_usage is not None else session.context_usage
        )
        choice: tuple[str, str] | None = None
        if first_turn and job.summary_of is not None:
            choice = (job.sid, job.summary_of)  # Requirement 10.8
        threshold = GROUP_SHOW_THRESHOLD if group else SHOW_THRESHOLD
        await self.notifier.send_reply(
            chat,
            split_reply(result.final_text),
            display_for(stored_usage, threshold),
            choice,
            reply_to=reply_to,
            group=group,
            choice_text=persona.choice_text,
        )

    async def _create_session(
        self, job: TextJob, chat: int, reply_to: int | None
    ) -> SessionRecord | None:
        """Name a new Session from its first message, then store it. None means it was not created.

        The naming request opens the Kiro Session, so the reply to the first message resumes it.
        """
        persona = resolve_persona(job.persona_key)
        workdir = self.runner.workdir_for(job.user_id)
        result = await self.runner.run_prompt(
            workdir, None, f"{persona.title_prompt}\n\n{job.text}"
        )
        if result.error is not None or result.final_text is None or not result.session_id:
            if result.refused:
                await self.notifier.send_text(chat, random.choice(persona.refusal_replies), reply_to)
                return None
            reason = result.error or "пустой ответ"
            await self.notifier.send_text(
                chat, f"{persona.new_session_failed_text}\nПричина: {reason}", reply_to
            )
            return None
        when = self.now()
        record = SessionRecord(
            sid=job.sid,
            name=self._session_name(job, persona, result.final_text),
            session_id=result.session_id,
            context_usage=result.context_usage,
            created_at=when,
            updated_at=when,
            persona=persona.key,
        )
        await self.store.add_session(job.user_id, record)
        await self.store.set_active(job.user_id, job.sid)
        return record

    def _session_name(self, job: TextJob, persona: Persona, reply: str) -> str:
        """The name the model gave, with the character postfix. The placeholder when it is unusable."""
        placeholder = job.pending_name or job.sid
        title = clean_title(reply)
        existing = [s.name for s in self.store.get_user(job.user_id).sessions]
        name = validate_session_name(title, existing) if title else None
        if name is None:
            return placeholder
        final = session_title(name, persona.display_name)
        if any(other.casefold() == final.casefold() for other in existing):
            return placeholder
        return final

    async def summary(self, job: SummaryJob) -> None:
        session = self.store.find_session(job.user_id, job.sid)
        if session is None or not session.session_id:
            await self.notifier.send_text(job.user_id, DEFAULT_PERSONA.no_session_text)
            return
        # The Summary_Prompt goes to the old Session. No new Session is created here (10.3).
        persona = resolve_persona(session.persona)
        workdir = self.runner.workdir_for(job.user_id)
        result = await self.runner.run_prompt(workdir, session.session_id, persona.summary_prompt)
        if result.error is not None or result.final_text is None:
            if result.refused:
                await self.notifier.send_text(job.user_id, random.choice(persona.refusal_replies))
                return
            await self.notifier.send_text(job.user_id, persona.summary_failed_text)
            return
        if result.context_usage is not None:
            await self.store.set_context_usage(job.user_id, job.sid, result.context_usage)
        await self.notifier.send_summary(job.user_id, result.final_text, job.sid)

    async def delete(self, job: DeleteJob) -> None:
        session = self.store.find_session(job.user_id, job.sid)
        if session is None:
            await self.notifier.send_text(job.user_id, DEFAULT_PERSONA.no_session_text)
            return
        persona = resolve_persona(session.persona)
        if session.session_id:
            workdir = self.runner.workdir_for(job.user_id)
            error = await self.runner.delete_session(workdir, session.session_id)
            if error is not None:
                await self.notifier.send_text(job.user_id, persona.delete_failed_text)
                return
        await self.store.remove_session(job.user_id, job.sid)
        await self.notifier.send_text(job.user_id, persona.delete_done)
