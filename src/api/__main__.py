"""Start the API: `python -m api` from the src directory."""

from __future__ import annotations

import logging
import sys

from aiohttp import web

from api.app import create_app
from api.settings import SettingsError, load_settings
from bot.envfile import EnvFileError
from bot.kiro_env import KiroEnvError


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = load_settings()
    except (SettingsError, EnvFileError, KiroEnvError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    web.run_app(create_app(settings), host=settings.host, port=settings.port, print=None)


if __name__ == "__main__":
    main()
