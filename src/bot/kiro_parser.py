"""Parser for `kiro-cli chat --v3 --output-format stream-json` (design D-1).

Verified shapes (amster, kiro-cli 3.5.0):
- Each line is one JSON object: {"type": ..., "data": {...}}.
- Session ID: data.sessionId on sessionUpdate and runFinished events.
- Reply: runFinished.data.finalText.
- Context usage is carried by sessionUpdate events under update._meta.kiro:
  * primary: kiro.kind == "context_usage" and kiro.usagePercentage
  * fallback: kiro.contextUsage.usagePercentage
  Values are percent points (0.79 means 0.79%).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RunResult:
    session_id: str | None
    final_text: str | None
    context_usage: float | None  # None when neither usage source appeared in the stream
    error: str | None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _usage_from_meta(meta: Any) -> tuple[float | None, float | None]:
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


def parse_stream(lines: Iterable[str]) -> RunResult:
    session_id: str | None = None
    final_text: str | None = None
    run_status: str | None = None
    run_error: str | None = None
    primary_usage: float | None = None
    fallback_usage: float | None = None

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except ValueError:
            continue  # kiro-cli may print non-JSON text; only JSON events count
        if not isinstance(event, Mapping):
            continue
        kind = event.get("type")
        data = event.get("data")
        if not isinstance(data, Mapping):
            continue

        if kind == "sessionUpdate":
            sid = data.get("sessionId")
            if session_id is None and isinstance(sid, str):
                session_id = sid
            update = data.get("update")
            if isinstance(update, Mapping):
                primary, fallback = _usage_from_meta(update.get("_meta"))
                if primary is not None:
                    primary_usage = primary
                if fallback is not None:
                    fallback_usage = fallback
        elif kind == "runFinished":
            sid = data.get("sessionId")
            if isinstance(sid, str):
                session_id = sid
            text = data.get("finalText")
            final_text = text if isinstance(text, str) else None
            status = data.get("status")
            run_status = status if isinstance(status, str) else None
        elif kind == "runError":
            message = data.get("message") or data.get("error") or "kiro-cli reported an error"
            run_error = str(message)

    usage = primary_usage if primary_usage is not None else fallback_usage

    if run_error is not None:
        return RunResult(session_id, None, usage, run_error)
    if final_text is None:
        return RunResult(session_id, None, usage, "kiro-cli returned no final text")
    if run_status is not None and run_status != "success":
        return RunResult(session_id, None, usage, f"kiro-cli run status: {run_status}")
    return RunResult(session_id, final_text, usage, None)
