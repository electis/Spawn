"""KIRO_API_KEY loading for the kiro-cli child process (Requirements 9.1, 9.2).

The key value is never logged, returned in an error message, or printed.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from bot.envfile import DEFAULT_ENV_FILE, EnvFileError, parse_env_file

KIRO_KEY = "KIRO_API_KEY"


class KiroEnvError(Exception):
    """Raised when the env file does not contain a usable key."""


def load_kiro_env(
    env_file: Path = DEFAULT_ENV_FILE,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a child-process environment with KIRO_API_KEY set."""
    try:
        values = parse_env_file(Path(env_file))
    except EnvFileError as exc:
        raise KiroEnvError(str(exc)) from exc
    key = values.get(KIRO_KEY, "")
    if not key:
        raise KiroEnvError(f"{KIRO_KEY} is missing from {env_file}")
    env = dict(os.environ if base_env is None else base_env)
    env[KIRO_KEY] = key
    return env
