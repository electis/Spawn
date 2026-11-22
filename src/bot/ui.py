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


ASK_NAME = "Как назовём эту беседу? Напиши имя."
NAME_INVALID = "Имя пустое, длиннее 40 символов или уже занято. Введите другое имя."
NO_ACTIVE = "Беседа не открыта. Откройте беседу из списка или создайте новую."
PENDING_NOT_CREATED = "Беседа ещё не создана. Отправьте первое сообщение в новой беседе."
FULL_TEXT = (
    "Беседа заполнена, новые сообщения сейчас не принимаю. "
    "Начни новую или удали старую."
)
NON_TEXT = "Поддерживаются только текстовые сообщения."
EMPTY_LIST = "Бесед пока нет. Начни новую, когда будешь готов."
LIST_TITLE = "Ваши беседы (новые сверху):"
OPEN_TEXT = "Беседа «{name}» открыта. Говори, что нужно."
DELETE_CONFIRM = "Удалить беседу «{name}»? Это нельзя отменить."
CANCELLED = "Удаление отменено."
NEW_READY = "Беседа «{name}» откроется, как только получу первое сообщение."
SUMMARY_STARTED = "Подвожу итог. Скоро будет готов."
SUMMARY_NAME_PROMPT = "Введите имя новой беседы для выжимки."
CHOICE_TEXT = "Какую беседу сделать текущей?"
CONTEXT_VALUE = "Беседа «{name}»: заполненность {value}."
CONTEXT_UNKNOWN = "Беседа «{name}»: данных о заполненности пока нет."
CONTEXT_PENDING = "Беседа «{name}» ещё не создана. Заполненность появится после первого ответа."
CONTEXT_FULL_NOTE = "Беседа заполнена, новые сообщения не принимаю. Очисти её или начни новую."


GROUP_NEW_TEXT = "Новый разговор открыт. Говори, с чего начнём"
GROUP_CLEARED_TEXT = "Контекст беседы очищен. Следующее сообщение начнёт новый разговор."
GROUP_FULL_TEXT = (
    "Беседа заполнена, сообщения сейчас не принимаю. "
    "Нажмите «Очистить контекст» или начните новую беседу."
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
    rows.append([button("Список бесед", MenuCb(action="list"))])
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
                button("Новая беседа", ChoiceCb(action="new", sid=new_sid)),
                button("Старая беседа", ChoiceCb(action="old", sid=old_sid)),
            ]
        ]
    )

# Command menu shown in private chats. Each command runs the same action as a button.
BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="new", description="Новая беседа"),
    BotCommand(command="sessions", description="Список бесед"),
    BotCommand(command="summary", description="Выжимка в новую беседу"),
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