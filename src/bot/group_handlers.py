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
from bot.names import session_title
from bot.persona import DEFAULT_PERSONA, PERSONAS, Persona, resolve_persona
from bot.ui import (
    PERSONA_CHOICE_TEXT,
    GroupPersonaCb,
    MenuCb,
    group_persona_choice_markup,
)

group_router = Router(name="kiro-bot-group")
group_router.message.filter(F.chat.type.in_(GROUP_TYPES))
group_router.callback_query.filter(F.message.chat.type.in_(GROUP_TYPES))


def _start_new_session(services: Services, key: int, persona: Persona = DEFAULT_PERSONA) -> PendingSession:
    """Create a pending Session with a placeholder name. It is named after its first message."""
    sid = secrets.token_hex(4)
    pending = PendingSession(
        sid=sid,
        name=session_title(f"Беседа {sid}", persona.display_name),
        persona=persona.key,
    )
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
async def on_group_new(message: Message) -> None:
    if message.from_user is None:
        return
    await message.reply(
        PERSONA_CHOICE_TEXT, reply_markup=group_persona_choice_markup(message.from_user.id)
    )


@group_router.callback_query(GroupPersonaCb.filter())
async def on_group_persona(
    query: CallbackQuery, callback_data: GroupPersonaCb, services: Services
) -> None:
    """Start the member's new Session in this group with the character they picked."""
    if query.from_user.id != callback_data.user_id or callback_data.key not in PERSONAS:
        await query.answer()  # someone else's picker, or an unknown character
        return
    if query.message is None:
        await query.answer()
        return
    persona = PERSONAS[callback_data.key]
    _start_new_session(services, store_key(query.message.chat.id, query.from_user.id), persona)
    await query.answer()
    await query.message.answer(persona.greeting_text or persona.group_new_text)


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
    session = services.store.find_session(key, sid)
    persona = resolve_persona(pending.persona if is_pending else (session.persona if session else None))
    job = TextJob(
        key,
        sid,
        prompt,
        pending_name=pending.name if is_pending else None,
        persona_key=persona.key,
        chat_id=message.chat.id,
        reply_to=message.message_id,
    )
    await services.dispatcher.submit(job)


@group_router.callback_query(MenuCb.filter(F.action == "clear"))
async def on_group_clear(query: CallbackQuery) -> None:
    await query.answer()
    if query.message is not None:
        await query.message.answer(
            PERSONA_CHOICE_TEXT, reply_markup=group_persona_choice_markup(query.from_user.id)
        )


# Catch-alls keep private handlers from answering group updates they were not written for.
@group_router.message()
async def on_group_other(message: Message) -> None:
    return None


@group_router.callback_query()
async def on_group_other_callback(query: CallbackQuery) -> None:
    await query.answer()
