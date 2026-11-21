"""Admin commands in private chat (ADMIN_USERNAME).

- /admin shows every group the bot is in (and allowed groups it has left), with a toggle
  button per group. The toggle switches the group between allowed and not allowed.
- The bot learns groups from my_chat_member updates. Only groups the bot is in can be allowed.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, Filter
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.groups import PRESENT_STATUSES, GroupRecord
from bot.roles import is_admin_username
from bot.state import Services

admin_router = Router(name="kiro-bot-admin")

GROUPS_TITLE = "Группы бота. Нажмите группу, чтобы разрешить или запретить участникам доступ."
NO_GROUPS = "Бот пока не состоит ни в одной группе. Добавьте его в группу, и она появится здесь."
ALLOWED_TEXT = "Группа разрешена."
DENIED_TEXT = "Группа запрещена."
NOT_IN_GROUP = "Бот не состоит в этой группе, разрешить её нельзя."
UNKNOWN_GROUP = "Группа не найдена."


class GroupCb(CallbackData, prefix="g"):
    action: str  # t = toggle allowed
    chat_id: int


class IsAdmin(Filter):
    async def __call__(self, obj: Message | CallbackQuery, services: Services) -> bool:
        user = obj.from_user
        return user is not None and is_admin_username(user.username, services.admin_username)


def _label(record: GroupRecord) -> str:
    mark = "✅" if record.allowed else "⬜"
    title = (record.title or str(record.chat_id))[:40]
    suffix = "" if record.present else " (бота нет)"
    return f"{mark} {title}{suffix}"


def groups_markup(records: list[GroupRecord]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=_label(record),
                callback_data=GroupCb(action="t", chat_id=record.chat_id).pack(),
            )
        ]
        for record in records
        if record.present or record.allowed
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _shown(records: list[GroupRecord]) -> list[GroupRecord]:
    return [r for r in records if r.present or r.allowed]


@admin_router.message(Command("admin"), F.chat.type == "private", IsAdmin())
async def on_admin_command(message: Message, services: Services) -> None:
    if services.groups is None:
        await message.answer(NO_GROUPS)
        return
    records = _shown(services.groups.groups())
    if not records:
        await message.answer(NO_GROUPS)
        return
    await message.answer(GROUPS_TITLE, reply_markup=groups_markup(records))


@admin_router.callback_query(GroupCb.filter(F.action == "t"), IsAdmin())
async def on_group_toggle(
    query: CallbackQuery, callback_data: GroupCb, services: Services
) -> None:
    if services.groups is None:
        await query.answer(UNKNOWN_GROUP, show_alert=True)
        return
    record = services.groups.get(callback_data.chat_id)
    if record is None:
        await query.answer(UNKNOWN_GROUP, show_alert=True)
        return
    if not record.allowed and not record.present:
        await query.answer(NOT_IN_GROUP, show_alert=True)
        return

    services.groups.set_allowed(record.chat_id, not record.allowed)
    if services.membership is not None:
        services.membership.clear()  # cached positives may belong to the old list
    await query.answer(DENIED_TEXT if record.allowed else ALLOWED_TEXT)
    if isinstance(query.message, Message):  # an inaccessible (old) message cannot be edited
        await query.message.edit_reply_markup(
            reply_markup=groups_markup(_shown(services.groups.groups()))
        )


@admin_router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated, services: Services) -> None:
    if event.chat.type not in {"group", "supergroup"} or services.groups is None:
        return
    present = event.new_chat_member.status in PRESENT_STATUSES
    services.groups.record_membership(event.chat.id, event.chat.title or "", present)
