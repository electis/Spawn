"""Parser and permission check for the KEY=VALUE env file (`src/.env`)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

DEFAULT_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class EnvFileError(Exception):
    """Raised when the env file is missing or readable by group or others."""


def parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def check_private(path: Path) -> None:
    """Refuse a file that group or other users can read (POSIX only)."""
    if os.name != "posix":
        return
    mode = Path(path).stat().st_mode
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        raise EnvFileError(f"{path} is readable by group or others; set mode 0600")


def parse_env_file(path: Path) -> dict[str, str]:
    """Read an env file after checking its permissions. Values are never included in errors."""
    path = Path(path)
    if not path.exists():
        raise EnvFileError(f"env file is missing: {path}")
    check_private(path)
    return parse_env_text(path.read_text(encoding="utf-8"))
