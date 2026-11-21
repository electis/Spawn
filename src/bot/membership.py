"""Membership check against the allowed groups (Requirements 1.5 to 1.10)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

logger = logging.getLogger(__name__)

MEMBER_STATUSES = frozenset({"creator", "administrator", "member"})


class MembershipChecker:
    def __init__(
        self,
        bot: Any,
        group_ids: Sequence[int] | Callable[[], Sequence[int]],
        clock: Callable[[], float] = time.monotonic,
        ttl: float = 300.0,
    ) -> None:
        """group_ids is either a fixed list or a callable that returns the current allowed list."""
        self._bot = bot
        if callable(group_ids):
            self._group_source: Callable[[], Sequence[int]] = group_ids
        else:
            fixed = tuple(group_ids)
            self._group_source = lambda: fixed
        self._clock = clock
        self._ttl = ttl
        self._positive_until: dict[int, float] = {}

    def clear(self) -> None:
        """Drop cached positives, for example after the admin changes the allowed groups."""
        self._positive_until.clear()

    async def _is_in_group(self, group_id: int, user_id: int) -> bool:
        try:
            member = await self._bot.get_chat_member(chat_id=group_id, user_id=user_id)
        except Exception as exc:  # noqa: BLE001 - one failing group counts as "not a member"
            logger.warning("getChatMember failed for group %s: %s", group_id, type(exc).__name__)
            return False
        return getattr(member, "status", None) in MEMBER_STATUSES

    async def is_member(self, user_id: int) -> bool:
        now = self._clock()
        expires = self._positive_until.get(user_id)
        if expires is not None and now < expires:
            return True

        # Every allowed group is queried, even after a positive answer (no early exit).
        groups = tuple(self._group_source())
        results = await asyncio.gather(*(self._is_in_group(g, user_id) for g in groups))
        positive = any(results)
        if positive:
            self._positive_until[user_id] = now + self._ttl
        else:
            self._positive_until.pop(user_id, None)  # negatives are never cached
        return positive
