"""Context gate for the Full state (Requirements 4.6 to 4.9; Assumption A-11).

Only ordinary text is gated. Summary and delete jobs always run.
"""

from __future__ import annotations

from bot.context_display import is_full
from bot.dispatcher import TextJob
from bot.session_store import SessionStore


class ContextGate:
    def __init__(self, store: SessionStore) -> None:
        self._store = store

    def is_full(self, user_id: int, sid: str) -> bool:
        session = self._store.find_session(user_id, sid)
        # A pending Session has no stored usage yet, so it is never full.
        return session is not None and is_full(session.context_usage)

    def blocks(self, job: TextJob) -> bool:
        return self.is_full(job.user_id, job.sid)
