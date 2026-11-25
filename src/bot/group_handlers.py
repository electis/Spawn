"""Handlers for allowed group chats: trigger messages, /new, and the Clear button.

Only groups the admin allowed reach this router (bot/middleware.py). Every other message or
callback in a group gets no reply. Each member has separate Sessions in each group
(bot/scope.py), and the bot answers as a reply to the triggering message.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from bot.dispatcher import TextJob
from bot.group_comment import (
    EMPTY_TEXT,
    TOO_MANY_TEXT,
    USAGE_TEXT,
    CommentRequestError,
    GroupMessage,
    build_prompt,
    parse_request,
    select_messages,
)
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

logger = logging.getLogger(__name__)

# Telegram's GroupAnonymousBot. A message an admin sends "as the group" has this as its author,
# so all such messages share one owner key, and the bot itself is marked as a bot.
GROUP_ANONYMOUS_BOT_ID = 1087968824

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
    """Start the picker's Session in this group with the character they picked.

    The picker is the member who sent /new. When /new was sent as the group, the picker is
    any member, and the Session belongs to the group's anonymous identity, the same identity
    that messages sent as the group use.
    """
    anonymous_pick = callback_data.user_id == GROUP_ANONYMOUS_BOT_ID
    if (not anonymous_pick and query.from_user.id != callback_data.user_id) or (
        callback_data.key not in PERSONAS
    ):
        logger.info(
            "group persona pick ignored: picker_is_issuer=%s known_key=%s",
            query.from_user.id == callback_data.user_id,
            callback_data.key in PERSONAS,
        )
        await query.answer()  # someone else's picker, or an unknown character
        return
    if query.message is None:
        logger.info("group persona pick without a message")
        await query.answer()
        return
    logger.info("group persona pick accepted")
    persona = PERSONAS[callback_data.key]
    _start_new_session(
        services, store_key(query.message.chat.id, callback_data.user_id), persona
    )
    await query.answer()
    await query.message.answer(persona.greeting_text or persona.group_new_text)


@group_router.message(Command("comment"))
async def on_group_comment(message: Message, services: Services, command: CommandObject) -> None:
    """Comment on the last N messages or on the messages of a period, in the member's Session."""
    if message.from_user is None:
        return
    try:
        request = parse_request(command.args)
    except CommentRequestError as exc:
        text = TOO_MANY_TEXT if str(exc) == "too_many" else USAGE_TEXT
        await message.reply(text)
        return
    now = datetime.now(timezone.utc)
    chosen = select_messages(services.group_log.messages(message.chat.id), request, now)
    if not chosen:
        await message.reply(EMPTY_TEXT)
        return
    await _submit_group_prompt(message, services, build_prompt(chosen))


@group_router.message(F.text)
async def on_group_text(message: Message, services: Services) -> None:
    user = message.from_user
    text = message.text or ""
    if user is None or text.startswith("/"):
        return  # other commands are private-only
    if not user.is_bot or user.id == GROUP_ANONYMOUS_BOT_ID:
        services.group_log.record(
            message.chat.id,
            GroupMessage(
                message_id=message.message_id,
                when=message.date.astimezone(timezone.utc),
                author=user.full_name,
                text=text,
            ),
        )
    addressed = mentions_bot(text, services.bot_username) or _replies_to_bot(
        message, services.bot_id
    )
    prompt = parse_trigger(text, addressed=addressed)
    if prompt is None:
        return  # not addressed to the bot: no reply and no Kiro call
    await _submit_group_prompt(message, services, prompt)


async def _submit_group_prompt(message: Message, services: Services, prompt: str) -> None:
    """Send a prompt to the member's Active_Session in this group, creating one if needed."""
    user = message.from_user
    assert user is not None
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
    logger.info("group callback not routed to a handler: prefix=%s", (query.data or "")[:3])
    await query.answer()
