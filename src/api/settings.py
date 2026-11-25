"""Settings for the API. Secrets come from the env file (src/.env) and are never printed."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from bot.envfile import DEFAULT_ENV_FILE, parse_env_file
from bot.kiro_env import KiroEnvError, load_kiro_env

API_KEY = "API_KEY"
DEFAULT_KIRO_BIN = "/root/.local/bin/kiro-cli"
DEFAULT_ACP_HOME = Path("/root/spawn-acp-home")
DEFAULT_WORKDIR = Path("/var/lib/kiro-bot/api-workdir")
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8731
DEFAULT_MODEL = "claude-haiku-5.5"
DEFAULT_MAX_PROCESSES = 4
DEFAULT_TIMEOUT_SECONDS = 600.0


class SettingsError(Exception):
    """A required setting is missing or invalid. Messages never include secret values."""


@dataclass(frozen=True)
class ApiSettings:
    api_key: str = field(repr=False)
    kiro_env: Mapping[str, str] = field(repr=False)
    kiro_bin: str
    acp_home: Path
    workdir: Path
    host: str
    port: int
    default_model: str
    max_processes: int
    timeout: float


def load_settings(
    env_file: Path = DEFAULT_ENV_FILE,
    environ: Mapping[str, str] | None = None,
) -> ApiSettings:
    env = dict(os.environ if environ is None else environ)
    try:
        kiro_env = load_kiro_env(env_file, base_env=env)
    except KiroEnvError as exc:
        raise SettingsError(str(exc)) from exc
    api_key = parse_env_file(Path(env_file)).get(API_KEY, "")
    if not api_key:
        raise SettingsError(f"{API_KEY} is missing from {env_file}")
    try:
        port = int(env.get("API_PORT", DEFAULT_PORT))
        max_processes = int(env.get("API_MAX_PROCESSES", DEFAULT_MAX_PROCESSES))
    except ValueError as exc:
        raise SettingsError("API_PORT and API_MAX_PROCESSES must be integers") from exc
    if max_processes < 1:
        raise SettingsError("API_MAX_PROCESSES must be at least 1")
    workdir = Path(env.get("API_WORKDIR", str(DEFAULT_WORKDIR)))
    workdir.mkdir(parents=True, exist_ok=True)
    return ApiSettings(
        api_key=api_key,
        kiro_env=kiro_env,
        kiro_bin=env.get("KIRO_BIN", DEFAULT_KIRO_BIN),
        acp_home=Path(env.get("ACP_HOME", str(DEFAULT_ACP_HOME))),
        workdir=workdir,
        host=env.get("API_HOST", DEFAULT_HOST),
        port=port,
        default_model=env.get("API_DEFAULT_MODEL", DEFAULT_MODEL),
        max_processes=max_processes,
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
