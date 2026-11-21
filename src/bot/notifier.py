"""Telegram output for the job processor (Notifier protocol in bot/jobs.py)."""

from __future__ import annotations

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from aiogram.types import InlineKeyboardMarkup

from bot.context_display import Display
from bot.splitter import split_reply
from bot.state import SessionNameStates
from bot.ui import ASK_NAME, CHOICE_TEXT, FULL_TEXT, choice_markup, options_markup
from bot.ui import MenuCb, button


class AiogramNotifier:
    def __init__(self, bot: Bot, storage: BaseStorage) -> None:
        self._bot = bot
        self._storage = storage

    def _fsm(self, user_id: int) -> FSMContext:
        # Private chats: chat id and user id are the same number.
        key = StorageKey(bot_id=self._bot.id, chat_id=user_id, user_id=user_id)
        return FSMContext(storage=self._storage, key=key)

    async def send_text(self, user_id: int, text: str) -> None:
        await self._bot.send_message(user_id, text)

    async def send_typing(self, user_id: int) -> None:
        await self._bot.send_chat_action(user_id, "typing")

    async def send_reply(
        self,
        user_id: int,
        chunks: list[str],
        display: Display,
        choice: tuple[str, str] | None,
    ) -> None:
        for chunk in chunks:
            await self._bot.send_message(user_id, chunk)
        if display.text is not None:
            markup = (
                InlineKeyboardMarkup(inline_keyboard=[[button("Сделать выжимку", MenuCb(action="sum"))]])
                if display.show_summarize
                else None
            )
            await self._bot.send_message(
                user_id, f"Заполненность контекста: {display.text}", reply_markup=markup
            )
        if choice is not None:
            new_sid, old_sid = choice
            await self._bot.send_message(
                user_id, CHOICE_TEXT, reply_markup=choice_markup(new_sid, old_sid)
            )

    async def send_summary(self, user_id: int, text: str, old_sid: str) -> None:
        for chunk in split_reply(text):
            await self._bot.send_message(user_id, chunk)
        await self._bot.send_message(user_id, ASK_NAME)
        fsm = self._fsm(user_id)
        await fsm.set_state(SessionNameStates.awaiting_name)
        await fsm.update_data(flow="summary", old_sid=old_sid, summary_text=text)

    async def send_full_options(self, user_id: int) -> None:
        await self._bot.send_message(user_id, FULL_TEXT, reply_markup=options_markup())
