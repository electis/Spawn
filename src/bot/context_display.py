"""Context usage display and the Full-state rule (Requirements 4.1 to 4.4, 10.1).
Raw values are percent points as reported by kiro-cli (design D-1). Shown values are raw x 10.
"""
from __future__ import annotations

from dataclasses import dataclass

FULL_THRESHOLD = 10.0  # raw value at which a Session is in Context_Full_State
SHOW_THRESHOLD = 5.0  # raw value at which usage is shown in a private chat
GROUP_SHOW_THRESHOLD = 7.0  # raw value at which usage is shown in a group, with a Clear button


@dataclass(frozen=True)
class Display:
    text: str | None  # None means nothing is shown
    show_summarize: bool


def display_for(raw: float | None, show_from: float = SHOW_THRESHOLD) -> Display:
    if raw is None:
        return Display(text="context size unavailable", show_summarize=False)
    if raw < show_from:
        return Display(text=None, show_summarize=False)
    return Display(text=f"{raw * 10:.1f}%", show_summarize=True)


def is_full(raw: float | None) -> bool:
    return raw is not None and raw >= FULL_THRESHOLD
