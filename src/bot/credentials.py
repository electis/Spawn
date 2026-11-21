"""Telegram token loading (Requirement 9.3). The token value is never logged."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from bot.envfile import DEFAULT_ENV_FILE, EnvFileError, parse_env_file

TOKEN_ENV = "TELEGRAM_BOT_TOKEN"


class CredentialError(Exception):
    """Raised when the token is missing or the env file is too open."""


def load_token(
    env: Mapping[str, str] | None = None,
    env_file: Path = DEFAULT_ENV_FILE,
) -> str:
    environ = os.environ if env is None else env
    from_env = environ.get(TOKEN_ENV, "").strip()
    if from_env:
        return from_env
    try:
        value = parse_env_file(Path(env_file)).get(TOKEN_ENV, "").strip()
    except EnvFileError as exc:
        raise CredentialError(str(exc)) from exc
    if not value:
        raise CredentialError(f"{TOKEN_ENV} is not set in the environment or {env_file}")
    return value
