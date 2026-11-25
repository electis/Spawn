"""Bot configuration loaded from /etc/kiro-bot/bot.toml (Requirement 1.10)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from bot.envfile import DEFAULT_ENV_FILE

DEFAULT_CONFIG_PATH = Path("/etc/kiro-bot/bot.toml")
DEFAULT_MEMBERSHIP_TTL_SECONDS = 300  # Assumption A-8
DEFAULT_ACP_HOME = Path("/root/spawn-acp-home")  # HOME for kiro-cli: holds the model choice


class ConfigError(Exception):
    """Raised when the configuration is missing or invalid."""


@dataclass(frozen=True)
class BotConfig:
    configured_group_ids: tuple[int, ...]
    store_path: Path
    workdir_root: Path
    kiro_bin: str
    env_file: Path  # holds TELEGRAM_BOT_TOKEN, KIRO_API_KEY, and ADMIN_USERNAME, mode 0600
    membership_ttl_seconds: float
    groups_path: Path  # legacy allowed-groups JSON, imported once into the database
    db_path: Path  # SQLite database: Sessions, groups, and the group message log
    acp_home: Path = DEFAULT_ACP_HOME  # kiro-cli HOME for ACP processes; chat.defaultModel lives here


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_config(text: str) -> BotConfig:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML: {exc}") from exc

    groups = data.get("configured_group_ids")
    if not isinstance(groups, list) or not all(_is_int(g) for g in groups):
        raise ConfigError("configured_group_ids must be a list of integers (may be empty)")

    ttl = data.get("membership_ttl_seconds", DEFAULT_MEMBERSHIP_TTL_SECONDS)
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl < 0:
        raise ConfigError("membership_ttl_seconds must be a non-negative number")

    try:
        return BotConfig(
            configured_group_ids=tuple(groups),
            store_path=Path(data["store_path"]),
            workdir_root=Path(data["workdir_root"]),
            kiro_bin=str(data.get("kiro_bin", "/root/.local/bin/kiro-cli")),
            env_file=Path(data.get("env_file", DEFAULT_ENV_FILE)),
            membership_ttl_seconds=float(ttl),
            groups_path=Path(
                data.get("groups_path", Path(data["store_path"]).parent / "groups.json")
            ),
            db_path=Path(
                data.get("db_path", Path(data["store_path"]).parent / "spawn.db")
            ),
            acp_home=Path(data.get("acp_home", DEFAULT_ACP_HOME)),
        )
    except KeyError as exc:
        raise ConfigError(f"missing required key: {exc.args[0]}") from exc


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> BotConfig:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    return parse_config(text)
