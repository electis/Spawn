"""Result type for one kiro-cli prompt, and the context usage helper.

Context usage is carried by session updates under update._meta.kiro, in two shapes:
- primary: kiro.kind == "context_usage" and kiro.usagePercentage
- fallback: kiro.contextUsage.usagePercentage
Values are percent points as reported by kiro-cli (0.79 means 0.79%). Design D-1.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

NO_FINAL_TEXT = "kiro-cli returned no final text"


@dataclass(frozen=True)
class RunResult:
    session_id: str | None
    final_text: str | None
    context_usage: float | None  # None when neither usage source appeared
    error: str | None
    refused: bool = False  # the model refused to answer (stopReason refusal)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def usage_from_meta(meta: Any) -> tuple[float | None, float | None]:
    """Return (primary, fallback) usage values found in one update's _meta."""
    if not isinstance(meta, Mapping):
        return None, None
    kiro = meta.get("kiro")
    if not isinstance(kiro, Mapping):
        return None, None
    primary = None
    if kiro.get("kind") == "context_usage":
        primary = _number(kiro.get("usagePercentage"))
    nested = kiro.get("contextUsage")
    fallback = _number(nested.get("usagePercentage")) if isinstance(nested, Mapping) else None
    return primary, fallback
