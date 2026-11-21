"""Session list pagination (Requirements 6.2, 6.3; Assumption A-15).

Order: most recent `updated_at` first. Ties are broken by `sid` ascending.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from bot.models import SessionRecord

PAGE_SIZE = 5


@dataclass(frozen=True)
class Page:
    items: list[SessionRecord]
    page: int
    has_prev: bool
    has_next: bool


def page_of(sessions: list[SessionRecord], page: int, size: int = PAGE_SIZE) -> Page:
    if size < 1:
        raise ValueError("size must be at least 1")
    # Stable sorts: first by sid, then by updated_at descending keeps sid order on ties.
    ordered = sorted(sorted(sessions, key=lambda s: s.sid), key=lambda s: s.updated_at, reverse=True)
    total_pages = max(1, math.ceil(len(ordered) / size))
    page = min(max(page, 0), total_pages - 1)
    start = page * size
    return Page(
        items=ordered[start : start + size],
        page=page,
        has_prev=page > 0,
        has_next=start + size < len(ordered),
    )
