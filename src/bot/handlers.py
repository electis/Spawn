"""Telegram handlers for private chats (Requirements 4.6, 4.7, 5, 6, 7, 10).

Only private messages reach these handlers: the middleware in bot/middleware.py drops the rest.
"""

from __future__ import annotations

import secrets

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.dispatcher import DeleteJob, SummaryJob, TextJob
from bot.pagination import page_of
from bot.names import validate_session_name
from bot.state import PendingSession, Services, SessionNameStates
from bot.ui import (
    ASK_NAME,
    CANCELLED,
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
    SUMMARY_STARTED,
    ChoiceCb,
    MenuCb,
    PageCb,
    SessionCb,
    delete_confirm_markup,
    options_markup,
    session_list_markup,
)

router = Router(name="kiro-bot")


def _reconcile_pending(services: Services, user_id: int) -> PendingSession | None:
    """Drop the pending Session once the first reply has stored it."""
    pending = services.pending.get(user_id)
    if pending is not None and services.store.find_session(user_id, pending.sid) is not None:
        services.pending.pop(user_id, None)
        return None
    return pending


def _active_sid(services: Services, user_id: int) -> str | None:
    pending = _reconcile_pending(services, user_id)
    if pending is not None:
        return pending.sid
    return services.store.get_user(user_id).active_sid


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


@router.message(SessionNameStates.awaiting_name, F.text)
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


@router.callback_query(MenuCb.filter(F.action == "sum"))
async def on_summarize(query: CallbackQuery, services: Services) -> None:
    user_id = query.from_user.id
    await query.answer()
    sid = _active_sid(services, user_id)
    message = query.message
    if sid is None:
        if message is not None:
            await message.answer(NO_ACTIVE)
        return
    pending = services.pending.get(user_id)
    if pending is not None and pending.sid == sid:
        if message is not None:
            await message.answer(PENDING_NOT_CREATED)
        return
    # Summarize is never blocked by the Full state (Requirement 10.16).
    await services.dispatcher.submit(SummaryJob(user_id, sid))


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
            await query.message.answer("Сессия удалена.")
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
