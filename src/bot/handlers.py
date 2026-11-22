"""Telegram handlers for private chats (Requirements 4.6, 4.7, 5, 6, 7, 10).

Only private messages reach these handlers: the middleware in bot/middleware.py drops the rest.
"""

from __future__ import annotations

import secrets

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.context_display import is_full
from bot.dispatcher import DeleteJob, SummaryJob, TextJob
from bot.pagination import page_of
from bot.names import validate_session_name
from bot.state import PendingSession, Services, SessionNameStates, active_sid
from bot.ui import (
    ASK_NAME,
    CANCELLED,
    CONTEXT_FULL_NOTE,
    CONTEXT_PENDING,
    CONTEXT_UNKNOWN,
    CONTEXT_VALUE,
    DELETE_CONFIRM,
    EMPTY_LIST,
    FULL_TEXT,
    LIST_TITLE,
    NAME_INVALID,
    NEW_READY,
    NO_ACTIVE,
    NON_TEXT,
    OPEN_TEXT,
    PENDING_NOT_CREATED,
    START_TEXT,
    SUMMARY_STARTED,
    ChoiceCb,
    MenuCb,
    PageCb,
    SessionCb,
    delete_confirm_markup,
    options_markup,
    commands_keyboard,
    session_list_markup,
)

router = Router(name="kiro-bot")


_active_sid = active_sid


def _list_names(services: Services, user_id: int) -> list[str]:
    return [s.name for s in services.store.get_user(user_id).sessions]


async def _send_list(message: Message, services: Services, user_id: int, page: int) -> None:
    sessions = services.store.get_user(user_id).sessions
    if not sessions:
        await message.answer(EMPTY_LIST, reply_markup=options_markup(show_summarize=False))
        return
    current = page_of(sessions, page)
    names = {s.sid: s.name for s in current.items}
    await message.answer(LIST_TITLE, reply_markup=session_list_markup(current, names))


# ---- Messages ---------------------------------------------------------------------------


@router.message(SessionNameStates.awaiting_name, F.text, ~F.text.startswith("/"))
async def on_session_name(message: Message, state: FSMContext, services: Services) -> None:
    user_id = message.from_user.id
    data = await state.get_data()
    name = validate_session_name(message.text or "", _list_names(services, user_id))
    if name is None:
        await message.answer(NAME_INVALID)
        return  # stay in awaiting_name, send no request (Requirement 5.3)

    await state.clear()
    sid = secrets.token_hex(4)
    if data.get("flow") == "summary":
        old_sid = data["old_sid"]
        summary_text = data["summary_text"]
        services.pending[user_id] = PendingSession(sid=sid, name=name, summary_of=old_sid)
        # The summary is the first message of the new Session, with no --resume-id (10.6).
        await services.dispatcher.submit(
            TextJob(user_id, sid, summary_text, pending_name=name, summary_of=old_sid)
        )
        await message.answer(SUMMARY_STARTED)
        return

    services.pending[user_id] = PendingSession(sid=sid, name=name)
    await message.answer(NEW_READY.format(name=name))


# ---- Command menu (same actions as the buttons) ------------------------------------------


@router.message(Command("start"))
async def on_command_start(message: Message) -> None:
    await message.answer(START_TEXT, reply_markup=commands_keyboard())


@router.message(Command("new"))
async def on_command_new(message: Message, state: FSMContext) -> None:
    await state.set_state(SessionNameStates.awaiting_name)
    await state.update_data(flow="new")
    await message.answer(ASK_NAME)


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
    sid = _active_sid(services, user_id)
    if sid is None:
        await message.answer(NO_ACTIVE, reply_markup=options_markup(show_summarize=False))
        return
    pending = services.pending.get(user_id)
    if pending is not None and pending.sid == sid:
        await message.answer(CONTEXT_PENDING.format(name=pending.name))
        return
    session = services.store.find_session(user_id, sid)
    if session is None:
        await message.answer(NO_ACTIVE, reply_markup=options_markup(show_summarize=False))
        return
    if session.context_usage is None:
        await message.answer(CONTEXT_UNKNOWN.format(name=session.name))
        return
    text = CONTEXT_VALUE.format(name=session.name, value=f"{session.context_usage * 10:.1f}%")
    if is_full(session.context_usage):
        text += "\n" + CONTEXT_FULL_NOTE
    await message.answer(text)


@router.message(F.text)
async def on_text(message: Message, services: Services) -> None:
    user_id = message.from_user.id
    sid = _active_sid(services, user_id)
    if sid is None:
        await message.answer(NO_ACTIVE, reply_markup=options_markup(show_summarize=False))
        return

    pending = services.pending.get(user_id)
    if pending is not None and pending.sid == sid:
        job = TextJob(
            user_id, sid, message.text or "", pending_name=pending.name, summary_of=pending.summary_of
        )
    else:
        if services.gate.is_full(user_id, sid):
            await message.answer(FULL_TEXT, reply_markup=options_markup())  # Requirement 4.7
            return
        job = TextJob(user_id, sid, message.text or "")
    await services.dispatcher.submit(job)


@router.message()
async def on_other(message: Message) -> None:
    await message.answer(NON_TEXT)


# ---- Menu buttons -----------------------------------------------------------------------


@router.callback_query(MenuCb.filter(F.action.in_({"new", "clear"})))
async def on_new(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SessionNameStates.awaiting_name)
    await state.update_data(flow="new")
    await query.answer()
    if query.message is not None:
        await query.message.answer(ASK_NAME)


@router.callback_query(MenuCb.filter(F.action == "list"))
async def on_list(query: CallbackQuery, services: Services) -> None:
    await query.answer()
    if query.message is not None:
        await _send_list(query.message, services, query.from_user.id, 0)


async def _start_summary(user_id: int, services: Services, reply_to: Message | None) -> None:
    sid = _active_sid(services, user_id)
    if sid is None:
        if reply_to is not None:
            await reply_to.answer(NO_ACTIVE)
        return
    pending = services.pending.get(user_id)
    if pending is not None and pending.sid == sid:
        if reply_to is not None:
            await reply_to.answer(PENDING_NOT_CREATED)
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
        await query.answer(NO_ACTIVE, show_alert=True)
        return
    services.pending.pop(user_id, None)  # an unsaved new Session is dropped on open
    await services.store.set_active(user_id, session.sid)  # local only, not queued (6.4)
    await query.answer()
    if query.message is not None:
        await query.message.answer(OPEN_TEXT.format(name=session.name))


@router.callback_query(SessionCb.filter(F.action == "d"))
async def on_delete_ask(query: CallbackQuery, callback_data: SessionCb, services: Services) -> None:
    session = services.store.find_session(query.from_user.id, callback_data.sid)
    await query.answer()
    if session is None or query.message is None:
        return
    await query.message.answer(
        DELETE_CONFIRM.format(name=session.name),
        reply_markup=delete_confirm_markup(session.sid),
    )


@router.callback_query(SessionCb.filter(F.action == "n"))
async def on_delete_cancel(query: CallbackQuery) -> None:
    await query.answer()
    if query.message is not None:
        await query.message.answer(CANCELLED)


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
            await query.message.answer("Беседа удалена.")
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
        await query.message.answer(OPEN_TEXT.format(name=session.name))
