"""Private-chat gate and membership check (Requirements 1.1 to 1.5).

Updates from group and supergroup chats reach the group handlers only when the admin allowed the group.
Updates from channels, and from groups that are not allowed, are dropped with no reply.
Private messages and callbacks from non-members get a refusal and never reach a handler.
The admin (ADMIN_USERNAME) is not checked for membership, so the admin can always manage groups.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.membership import MembershipChecker
from bot.scope import GROUP_TYPES
from bot.roles import is_admin_username

REFUSAL_TEXT = "Бот доступен только участникам групп, с которыми он связан."


class PrivateMembershipMiddleware(BaseMiddleware):
    def __init__(
        self,
        checker: MembershipChecker,
        refusal_text: str = REFUSAL_TEXT,
        admin_username: str | None = None,
        group_open: Callable[[int], bool] | None = None,
    ) -> None:
        super().__init__()
        self._checker = checker
        self._refusal = refusal_text
        self._admin_username = admin_username
        self._group_open = group_open

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            chat, user = event.chat, event.from_user
        elif isinstance(event, CallbackQuery):
            chat = event.message.chat if event.message is not None else None
            user = event.from_user
        else:
            return await handler(event, data)

        if chat is not None and chat.type in GROUP_TYPES:
            if self._group_open is not None and self._group_open(chat.id):
                return await handler(event, data)
            return None  # a group the admin has not allowed gets no reply
        if chat is None or chat.type != "private":
            return None  # Requirement 1.1: channels and other chat types are ignored

        if user is not None and is_admin_username(user.username, self._admin_username):
            return await handler(event, data)

        if user is None or not await self._checker.is_member(user.id):
            if isinstance(event, Message):
                await event.answer(self._refusal)
            else:
                await event.answer(self._refusal, show_alert=True)
            return None  # Requirement 1.5: no handler and no Kiro call

        return await handler(event, data)
