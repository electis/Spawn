"""Handlers for allowed group chats: trigger messages, /new, and the Clear button.

Only groups the admin allowed reach this router (bot/middleware.py). Every other message or
callback in a group gets no reply. Each member has separate Sessions in each group
(bot/scope.py), and the bot answers as a reply to the triggering message.
"""

from __future__ import annotations

import secrets

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.dispatcher import TextJob
from bot.group_trigger import mentions_bot, parse_trigger
from bot.scope import GROUP_TYPES, store_key
from bot.state import PendingSession, Services, active_sid
from bot.ui import GROUP_CLEARED_TEXT, GROUP_NEW_TEXT, MenuCb

group_router = Router(name="kiro-bot-group")
group_router.message.filter(F.chat.type.in_(GROUP_TYPES))
group_router.callback_query.filter(F.message.chat.type.in_(GROUP_TYPES))


def _start_new_session(services: Services, key: int) -> PendingSession:
    """Create a pending Session with a generated name. It is stored after its first reply."""
    sid = secrets.token_hex(4)
    pending = PendingSession(sid=sid, name=f"Беседа {sid}")
    services.pending[key] = pending
    return pending


def _replies_to_bot(message: Message, bot_id: int | None) -> bool:
    replied = message.reply_to_message
    return (
        replied is not None
        and replied.from_user is not None
        and bot_id is not None
        and replied.from_user.id == bot_id
    )


@group_router.message(Command("new"))
async def on_group_new(message: Message, services: Services) -> None:
    if message.from_user is None:
        return
    _start_new_session(services, store_key(message.chat.id, message.from_user.id))
    await message.reply(GROUP_NEW_TEXT)


@group_router.message(F.text)
async def on_group_text(message: Message, services: Services) -> None:
    user = message.from_user
    text = message.text or ""
    if user is None or text.startswith("/"):
        return  # other commands are private-only
    addressed = mentions_bot(text, services.bot_username) or _replies_to_bot(
        message, services.bot_id
    )
    prompt = parse_trigger(text, addressed=addressed)
    if prompt is None:
        return  # not addressed to the bot: no reply and no Kiro call

    key = store_key(message.chat.id, user.id)
    sid = active_sid(services, key)
    if sid is None:
        sid = _start_new_session(services, key).sid
    pending = services.pending.get(key)
    is_pending = pending is not None and pending.sid == sid
    job = TextJob(
        key,
        sid,
        prompt,
        pending_name=pending.name if is_pending else None,
        chat_id=message.chat.id,
        reply_to=message.message_id,
    )
    await services.dispatcher.submit(job)


@group_router.callback_query(MenuCb.filter(F.action == "clear"))
async def on_group_clear(query: CallbackQuery, services: Services) -> None:
    if query.message is None:
        await query.answer()
        return
    _start_new_session(services, store_key(query.message.chat.id, query.from_user.id))
    await query.answer(GROUP_CLEARED_TEXT)


# Catch-alls keep private handlers from answering group updates they were not written for.
@group_router.message()
async def on_group_other(message: Message) -> None:
    return None


@group_router.callback_query()
async def on_group_other_callback(query: CallbackQuery) -> None:
    await query.answer()
