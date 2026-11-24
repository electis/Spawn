"""Telegram handlers for private chats (Requirements 4.6, 4.7, 5, 6, 7, 10).

Only private messages reach these handlers: the middleware in bot/middleware.py drops the rest.
Texts come from the character of the conversation the User is in (bot/state.py: persona_for).
"""

from __future__ import annotations

import secrets

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.context_display import is_full
from bot.dispatcher import DeleteJob, SummaryJob, TextJob
from bot.names import session_title
from bot.pagination import page_of
from bot.persona import DEFAULT_PERSONA, PERSONAS, resolve_persona
from bot.state import PendingSession, Services, SessionNameStates, active_sid, persona_for
from bot.ui import (
    COMMANDS_HINT,
    PERSONA_CHOICE_TEXT,
    ChoiceCb,
    MenuCb,
    PageCb,
    PersonaCb,
    SessionCb,
    commands_keyboard,
    delete_confirm_markup,
    options_markup,
    persona_choice_markup,
    session_list_markup,
)

router = Router(name="kiro-bot")


_active_sid = active_sid


def _ensure_first_session(services: Services, user_id: int) -> bool:
    """Open a pending conversation for a User who has none yet. Returns True when one was opened.

    Users with existing conversations are not changed, so an empty list still shows no_active.
    """
    if services.store.get_user(user_id).sessions or _active_sid(services, user_id) is not None:
        return False
    sid = secrets.token_hex(4)
    persona = DEFAULT_PERSONA
    name = session_title(f"Беседа {sid}", persona.display_name)
    services.pending[user_id] = PendingSession(sid=sid, name=name, persona=persona.key)
    return True


async def _send_list(message: Message, services: Services, user_id: int, page: int) -> None:
    persona = persona_for(services, user_id)
    sessions = services.store.get_user(user_id).sessions
    if not sessions:
        await message.answer(persona.empty_list, reply_markup=options_markup(show_summarize=False))
        return
    current = page_of(sessions, page)
    names = {s.sid: s.name for s in current.items}
    await message.answer(persona.list_title, reply_markup=session_list_markup(current, names))


# ---- New conversation: character first, then the first message ---------------------------


@router.callback_query(SessionNameStates.awaiting_persona, PersonaCb.filter())
async def on_persona_choice(
    query: CallbackQuery, callback_data: PersonaCb, state: FSMContext, services: Services
) -> None:
    """Open a new Session with the picked character. Its name comes from the first exchange."""
    if callback_data.key not in PERSONAS:
        await query.answer()
        return
    data = await state.get_data()
    await state.clear()
    await query.answer()
    user_id = query.from_user.id
    persona = PERSONAS[callback_data.key]
    sid = secrets.token_hex(4)
    placeholder = session_title(f"Беседа {sid}", persona.display_name)
    if query.message is not None and persona.start_text:
        await query.message.answer(persona.start_text)
    if data.get("flow") == "summary":
        old_sid = data["old_sid"]
        summary_text = data["summary_text"]
        services.pending[user_id] = PendingSession(
            sid=sid, name=placeholder, summary_of=old_sid, persona=persona.key
        )
        # The summary is the first message of the new Session, with no --resume-id (10.6).
        await services.dispatcher.submit(
            TextJob(
                user_id,
                sid,
                summary_text,
                pending_name=placeholder,
                summary_of=old_sid,
                persona_key=persona.key,
            )
        )
        if query.message is not None:
            await query.message.answer(persona.summary_started)
        return

    services.pending[user_id] = PendingSession(sid=sid, name=placeholder, persona=persona.key)


# ---- Command menu (same actions as the buttons) ------------------------------------------


@router.message(Command("start"))
async def on_command_start(message: Message, state: FSMContext) -> None:
    """/start opens a new conversation, as /new does, and shows the command keyboard."""
    await state.set_state(SessionNameStates.awaiting_persona)
    await state.update_data(flow="new")
    await message.answer(PERSONA_CHOICE_TEXT, reply_markup=persona_choice_markup())
    await message.answer(COMMANDS_HINT, reply_markup=commands_keyboard())


@router.message(Command("new"))
async def on_command_new(message: Message, state: FSMContext) -> None:
    await state.set_state(SessionNameStates.awaiting_persona)
    await state.update_data(flow="new")
    await message.answer(PERSONA_CHOICE_TEXT, reply_markup=persona_choice_markup())


@router.message(Command("sessions"))
async def on_command_sessions(message: Message, services: Services) -> None:
    await _send_list(message, services, message.from_user.id, 0)


@router.message(Command("summary"))
async def on_command_summary(message: Message, services: Services) -> None:
    await _start_summary(message.from_user.id, services, message)


@router.message(Command("context"))
async def on_context(message: Message, services: Services) -> None:
    """Show the context size of the Active_Session, whatever its value (not gated)."""
    user_id = message.from_user.id
    persona = persona_for(services, user_id)
    sid = _active_sid(services, user_id)
    if sid is None:
        await message.answer(persona.no_active, reply_markup=options_markup(show_summarize=False))
        return
    pending = services.pending.get(user_id)
    if pending is not None and pending.sid == sid:
        await message.answer(persona.context_pending.format(name=pending.name))
        return
    session = services.store.find_session(user_id, sid)
    if session is None:
        await message.answer(persona.no_active, reply_markup=options_markup(show_summarize=False))
        return
    if session.context_usage is None:
        await message.answer(persona.context_unknown.format(name=session.name))
        return
    text = persona.context_value.format(
        name=session.name, value=f"{session.context_usage * 10:.1f}%"
    )
    if is_full(session.context_usage):
        text += "\n" + persona.context_full_note
    await message.answer(text)


@router.message(F.text)
async def on_text(message: Message, services: Services) -> None:
    user_id = message.from_user.id
    sid = _active_sid(services, user_id)
    if sid is None:
        if not _ensure_first_session(services, user_id):
            persona = persona_for(services, user_id)
            await message.answer(persona.no_active, reply_markup=options_markup(show_summarize=False))
            return
        sid = _active_sid(services, user_id)

    pending = services.pending.get(user_id)
    if pending is not None and pending.sid == sid:
        job = TextJob(
            user_id,
            sid,
            message.text or "",
            pending_name=pending.name,
            summary_of=pending.summary_of,
            persona_key=pending.persona,
        )
    else:
        persona = persona_for(services, user_id)
        if services.gate.is_full(user_id, sid):
            await message.answer(persona.full_text, reply_markup=options_markup())  # Requirement 4.7
            return
        job = TextJob(user_id, sid, message.text or "", persona_key=persona.key)
    await services.dispatcher.submit(job)


@router.message()
async def on_other(message: Message, services: Services) -> None:
    await message.answer(persona_for(services, message.from_user.id).non_text)


# ---- Menu buttons -----------------------------------------------------------------------


@router.callback_query(MenuCb.filter(F.action.in_({"new", "clear"})))
async def on_new(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SessionNameStates.awaiting_persona)
    await state.update_data(flow="new")
    await query.answer()
    if query.message is not None:
        await query.message.answer(PERSONA_CHOICE_TEXT, reply_markup=persona_choice_markup())


@router.callback_query(MenuCb.filter(F.action == "list"))
async def on_list(query: CallbackQuery, services: Services) -> None:
    await query.answer()
    if query.message is not None:
        await _send_list(query.message, services, query.from_user.id, 0)


async def _start_summary(user_id: int, services: Services, reply_to: Message | None) -> None:
    persona = persona_for(services, user_id)
    sid = _active_sid(services, user_id)
    if sid is None:
        if reply_to is not None:
            await reply_to.answer(persona.no_active)
        return
    pending = services.pending.get(user_id)
    if pending is not None and pending.sid == sid:
        if reply_to is not None:
            await reply_to.answer(persona.pending_not_created)
        return
    # Summarize is never blocked by the Full state (Requirement 10.16).
    await services.dispatcher.submit(SummaryJob(user_id, sid))


@router.callback_query(MenuCb.filter(F.action == "sum"))
async def on_summarize(query: CallbackQuery, services: Services) -> None:
    await query.answer()
    reply_to = query.message if isinstance(query.message, Message) else None
    await _start_summary(query.from_user.id, services, reply_to)


# ---- Session list -----------------------------------------------------------------------


@router.callback_query(PageCb.filter())
async def on_page(query: CallbackQuery, callback_data: PageCb, services: Services) -> None:
    await query.answer()
    if query.message is not None:
        await _send_list(query.message, services, query.from_user.id, callback_data.page)


@router.callback_query(SessionCb.filter(F.action == "o"))
async def on_open(query: CallbackQuery, callback_data: SessionCb, services: Services) -> None:
    user_id = query.from_user.id
    session = services.store.find_session(user_id, callback_data.sid)
    if session is None:
        await query.answer(persona_for(services, user_id).no_active, show_alert=True)
        return
    services.pending.pop(user_id, None)  # an unsaved new Session is dropped on open
    await services.store.set_active(user_id, session.sid)  # local only, not queued (6.4)
    await query.answer()
    if query.message is not None:
        persona = resolve_persona(session.persona)
        await query.message.answer(persona.open_text.format(name=session.name))


@router.callback_query(SessionCb.filter(F.action == "d"))
async def on_delete_ask(query: CallbackQuery, callback_data: SessionCb, services: Services) -> None:
    session = services.store.find_session(query.from_user.id, callback_data.sid)
    await query.answer()
    if session is None or query.message is None:
        return
    persona = resolve_persona(session.persona)
    await query.message.answer(
        persona.delete_confirm.format(name=session.name),
        reply_markup=delete_confirm_markup(session.sid),
    )


@router.callback_query(SessionCb.filter(F.action == "n"))
async def on_delete_cancel(query: CallbackQuery, services: Services) -> None:
    await query.answer()
    if query.message is not None:
        await query.message.answer(persona_for(services, query.from_user.id).cancelled)


@router.callback_query(SessionCb.filter(F.action == "y"))
async def on_delete_confirm(
    query: CallbackQuery, callback_data: SessionCb, services: Services
) -> None:
    user_id = query.from_user.id
    await query.answer()
    pending = services.pending.get(user_id)
    if pending is not None and pending.sid == callback_data.sid:
        # Not stored yet and no session_id: drop it locally with no Kiro call (15.2).
        services.pending.pop(user_id, None)
        if query.message is not None:
            await query.message.answer(resolve_persona(pending.persona).delete_done)
        return
    await services.dispatcher.submit(DeleteJob(user_id, callback_data.sid))


# ---- Summary follow-up ------------------------------------------------------------------


@router.callback_query(ChoiceCb.filter(F.action.in_({"new", "old"})))
async def on_choice(query: CallbackQuery, callback_data: ChoiceCb, services: Services) -> None:
    user_id = query.from_user.id
    session = services.store.find_session(user_id, callback_data.sid)
    await query.answer()
    if session is None:
        return
    await services.store.set_active(user_id, session.sid)
    if query.message is not None:
        persona = resolve_persona(session.persona)
        await query.message.answer(persona.open_text.format(name=session.name))
