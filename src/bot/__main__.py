"""Entry point: load config and secrets, build the services, and start aiogram polling."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommandScopeAllPrivateChats

from bot.admin import admin_router
from bot.config import ConfigError, load_config
from bot.credentials import CredentialError, load_token
from bot.dispatcher import UserDispatcher
from bot.envfile import EnvFileError, parse_env_file
from bot.db import Database, DatabaseError
from bot.gate import ContextGate
from bot.group_comment import GroupLog
from bot.group_handlers import group_router
from bot.legacy_import import import_legacy
from bot.groups import GroupRegistry, GroupsError
from bot.handlers import router
from bot.jobs import JobProcessor
from bot.kiro_env import KIRO_KEY, KiroEnvError, load_kiro_env
from bot.kiro_runner import KiroRunner
from bot.membership import MembershipChecker
from bot.middleware import PrivateMembershipMiddleware
from bot.notifier import AiogramNotifier
from bot.redact import SecretRedactor
from bot.session_store import CorruptStoreError, SessionStore
from bot.state import Services
from bot.ui import BOT_COMMANDS

ADMIN_KEY = "ADMIN_USERNAME"


async def run() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    log = logging.getLogger(__name__)
    try:
        config = load_config()
        token = load_token(env_file=config.env_file)
        kiro_env = load_kiro_env(config.env_file)
        admin_username = parse_env_file(config.env_file).get(ADMIN_KEY, "").strip() or None
    except (ConfigError, CredentialError, KiroEnvError, EnvFileError) as exc:
        print(f"startup error: {exc}", file=sys.stderr)
        return 1

    if admin_username is None:
        log.warning("%s is not set in the env file; nobody can manage groups", ADMIN_KEY)

    # Attach the filter to handlers: records from child loggers do not pass logger-level filters.
    redactor = SecretRedactor([token, kiro_env[KIRO_KEY]])
    for handler in logging.getLogger().handlers:
        handler.addFilter(redactor)

    try:
        db = Database(config.db_path)
        import_legacy(
            db, config.store_path, config.groups_path, config.configured_group_ids
        )
    except (DatabaseError, CorruptStoreError, GroupsError) as exc:
        log.error("%s", exc)
        return 1
    store = SessionStore(db)
    groups = GroupRegistry(db)

    bot = Bot(token=token)
    me = await bot.get_me()
    storage = MemoryStorage()
    checker = MembershipChecker(bot, groups.allowed_ids, ttl=config.membership_ttl_seconds)
    runner = KiroRunner(config.kiro_bin, kiro_env, config.workdir_root, acp_home=config.acp_home)
    notifier = AiogramNotifier(bot, storage)
    processor = JobProcessor(store, runner, notifier)
    gate = ContextGate(store)
    dispatcher = UserDispatcher(
        processor.handle,
        is_blocked=gate.blocks,
        on_blocked=notifier.send_blocked,
        show_typing=notifier.send_typing,
    )
    services = Services(
        store=store,
        gate=gate,
        dispatcher=dispatcher,
        groups=groups,
        membership=checker,
        admin_username=admin_username,
        bot_id=me.id,
        bot_username=me.username,
        group_log=GroupLog(db),
    )

    dp = Dispatcher(storage=storage)
    membership = PrivateMembershipMiddleware(
        checker, admin_username=admin_username, group_open=services.group_is_open
    )
    dp.message.outer_middleware(membership)
    dp.callback_query.outer_middleware(membership)
    dp.include_router(admin_router)
    dp.include_router(group_router)
    dp.include_router(router)

    await bot.set_my_commands(BOT_COMMANDS, scope=BotCommandScopeAllPrivateChats())

    try:
        await dp.start_polling(
            bot,
            services=services,
            allowed_updates=["message", "callback_query", "my_chat_member"],
        )
    finally:
        await runner.close()
        db.close()
    return 0


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
