"""Keyboards, callback data, and user-facing texts (Requirements 4.7, 5, 6, 7, 10).

Callback data carries only short ids and actions, never a Session_Name (Requirement 6.4).
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from bot.pagination import Page


class SessionCb(CallbackData, prefix="s"):
    action: str  # o = open, d = ask delete, y = confirm delete, n = cancel delete
    sid: str


class PageCb(CallbackData, prefix="p"):
    page: int


class MenuCb(CallbackData, prefix="m"):
    action: str  # new, clear, list, sum


class ChoiceCb(CallbackData, prefix="c"):
    action: str  # new or old
    sid: str


ASK_NAME = "Введите имя новой сессии (от 1 до 40 символов, без повтора имени)."
NAME_INVALID = "Имя пустое, длиннее 40 символов или уже занято. Введите другое имя."
NO_ACTIVE = "Сессия не открыта. Откройте сессию из списка или создайте новую."
PENDING_NOT_CREATED = "Сессия ещё не создана. Отправьте первое сообщение в новой сессии."
FULL_TEXT = (
    "Сессия заполнена, обычные сообщения сейчас не принимаются. "
    "Сделайте выжимку, начните новую беседу или откройте другую сессию из списка."
)
NON_TEXT = "Поддерживаются только текстовые сообщения."
EMPTY_LIST = "Сессий пока нет. Начните новую беседу."
LIST_TITLE = "Ваши сессии (новые сверху):"
OPEN_TEXT = "Открыта сессия «{name}»."
DELETE_CONFIRM = "Удалить сессию «{name}»? Это действие нельзя отменить."
CANCELLED = "Удаление отменено."
NEW_READY = "Сессия «{name}» будет активной после первого ответа. Напишите сообщение."
SUMMARY_STARTED = "Делаю выжимку. Когда она будет готова, введите имя новой сессии."
SUMMARY_NAME_PROMPT = "Введите имя новой сессии для выжимки."
CHOICE_TEXT = "Какую сессию сделать активной?"
CONTEXT_VALUE = "Сессия «{name}»: заполненность {value}."
CONTEXT_UNKNOWN = "Сессия «{name}»: данных о заполненности пока нет."
CONTEXT_PENDING = "Сессия «{name}» ещё не создана. Заполненность появится после первого ответа."
CONTEXT_FULL_NOTE = "Сессия заполнена: обычные сообщения не принимаются. Сделайте выжимку или начните новую беседу."


GROUP_NEW_TEXT = "Новая беседа начата. Следующее сообщение начнёт её."
GROUP_CLEARED_TEXT = "Контекст очищен. Следующее сообщение начнёт новую беседу."
GROUP_FULL_TEXT = (
    "Сессия заполнена, сообщения сейчас не принимаются. "
    "Нажмите «Очистить контекст», чтобы начать новую беседу."
)

def button(text: str, data: CallbackData) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data.pack())


def options_markup(show_summarize: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if show_summarize:
        rows.append([button("Сделать выжимку", MenuCb(action="sum"))])
    rows.append(
        [
            button("Новая беседа", MenuCb(action="new")),
            button("Очистить контекст", MenuCb(action="clear")),
        ]
    )
    rows.append([button("Список сессий", MenuCb(action="list"))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def clear_markup() -> InlineKeyboardMarkup:
    """Single Clear button shown in groups, where summaries are not offered."""
    return InlineKeyboardMarkup(inline_keyboard=[[button("Очистить контекст", MenuCb(action="clear"))]])

def session_list_markup(page: Page, names: dict[str, str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for session in page.items:
        rows.append(
            [
                button(names[session.sid], SessionCb(action="o", sid=session.sid)),
                button("Удалить", SessionCb(action="d", sid=session.sid)),
            ]
        )
    nav: list[InlineKeyboardButton] = []
    if page.has_prev:
        nav.append(button("« Назад", PageCb(page=page.page - 1)))
    if page.has_next:
        nav.append(button("Далее »", PageCb(page=page.page + 1)))
    if nav:
        rows.append(nav)
    rows.append([button("Новая беседа", MenuCb(action="new"))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def delete_confirm_markup(sid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button("Да, удалить", SessionCb(action="y", sid=sid)),
                button("Отмена", SessionCb(action="n", sid=sid)),
            ]
        ]
    )


def choice_markup(new_sid: str, old_sid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button("Новая сессия", ChoiceCb(action="new", sid=new_sid)),
                button("Старая сессия", ChoiceCb(action="old", sid=old_sid)),
            ]
        ]
    )

# Command menu shown in private chats. Each command runs the same action as a button.
BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="new", description="Новая беседа"),
    BotCommand(command="sessions", description="Список сессий"),
    BotCommand(command="summary", description="Выжимка в новую сессию"),
    BotCommand(command="context", description="Заполненность контекста"),
]
START_TEXT = (
    "Тьма учила меня говорить громко, но в личных разговорах хватает и тихих слов. "
    "Ты написал напрямую, значит, дело у тебя не для всех. "
    "Говори, что нужно, без предисловий. Я выслушаю и отвечу честно."
)


def commands_keyboard() -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=f"/{c.command}") for c in BOT_COMMANDS[:2]],
            [KeyboardButton(text=f"/{c.command}") for c in BOT_COMMANDS[2:]]]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)