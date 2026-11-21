"""Log filter that masks secret values (Requirement 9.2).

Log stdin text as its length only. That is the caller's job, not this filter's.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

MASK = "***"


class SecretRedactor(logging.Filter):
    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        # Longest first, so a secret that contains another one is masked whole.
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def clean(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, MASK)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a bad format string must not leak args
            message = str(record.msg)
        record.msg = self.clean(message)
        record.args = ()
        if record.exc_info:
            formatted = logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
            record.exc_text = self.clean(formatted)
        elif record.exc_text:
            record.exc_text = self.clean(record.exc_text)
        return True
