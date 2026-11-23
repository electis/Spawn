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
from bot.persona import PERSONAS


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


class PersonaCb(CallbackData, prefix="pc"):
    key: str  # a key of bot.persona.PERSONAS


class GroupPersonaCb(CallbackData, prefix="gp"):
    key: str  # a key of bot.persona.PERSONAS
    user_id: int  # only this member can pick; the pick starts their Session in the group





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


def commands_keyboard() -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=f"/{c.command}") for c in BOT_COMMANDS[:2]],
            [KeyboardButton(text=f"/{c.command}") for c in BOT_COMMANDS[2:]]]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


PERSONA_CHOICE_TEXT = "С кем начнём новую беседу? Выбери персонажа."


def group_persona_choice_markup(user_id: int) -> InlineKeyboardMarkup:
    """Character buttons for a group. Only user_id can press them."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button(persona.display_name, GroupPersonaCb(key=key, user_id=user_id))]
            for key, persona in PERSONAS.items()
        ]
    )


def persona_choice_markup() -> InlineKeyboardMarkup:
    """One button per registered character. Its name becomes the postfix of the Session name."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button(persona.display_name, PersonaCb(key=key))]
            for key, persona in PERSONAS.items()
        ]
    )

COMMANDS_HINT = "Команды доступны на клавиатуре под полем ввода."
