"""Telegram output for the job processor (Notifier protocol in bot/jobs.py)."""

from __future__ import annotations

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from aiogram.types import InlineKeyboardMarkup, ReplyParameters

from bot.context_display import Display
from bot.dispatcher import TextJob
from bot.splitter import split_reply
from bot.state import SessionNameStates
from bot.ui import (
    ASK_NAME,
    CHOICE_TEXT,
    FULL_TEXT,
    GROUP_FULL_TEXT,
    MenuCb,
    button,
    choice_markup,
    clear_markup,
    options_markup,
)


def _reply(reply_to: int | None) -> ReplyParameters | None:
    return None if reply_to is None else ReplyParameters(message_id=reply_to)


class AiogramNotifier:
    def __init__(self, bot: Bot, storage: BaseStorage) -> None:
        self._bot = bot
        self._storage = storage

    def _fsm(self, user_id: int) -> FSMContext:
        # Private chats: chat id and user id are the same number.
        key = StorageKey(bot_id=self._bot.id, chat_id=user_id, user_id=user_id)
        return FSMContext(storage=self._storage, key=key)

    async def send_text(self, chat_id: int, text: str, reply_to: int | None = None) -> None:
        await self._bot.send_message(chat_id, text, reply_parameters=_reply(reply_to))

    async def send_typing(self, chat_id: int) -> None:
        await self._bot.send_chat_action(chat_id, "typing")

    async def send_reply(
        self,
        chat_id: int,
        chunks: list[str],
        display: Display,
        choice: tuple[str, str] | None,
        reply_to: int | None = None,
        group: bool = False,
    ) -> None:
        for index, chunk in enumerate(chunks):
            await self._bot.send_message(
                chat_id, chunk, reply_parameters=_reply(reply_to) if index == 0 else None
            )
        if display.text is not None:
            if group:
                markup: InlineKeyboardMarkup | None = clear_markup()
            elif display.show_summarize:
                markup = InlineKeyboardMarkup(
                    inline_keyboard=[[button("Сделать выжимку", MenuCb(action="sum"))]]
                )
            else:
                markup = None
            await self._bot.send_message(
                chat_id, f"Заполненность контекста: {display.text}", reply_markup=markup
            )
        if choice is not None:
            new_sid, old_sid = choice
            await self._bot.send_message(
                chat_id, CHOICE_TEXT, reply_markup=choice_markup(new_sid, old_sid)
            )

    async def send_summary(self, user_id: int, text: str, old_sid: str) -> None:
        for chunk in split_reply(text):
            await self._bot.send_message(user_id, chunk)
        await self._bot.send_message(user_id, ASK_NAME)
        fsm = self._fsm(user_id)
        await fsm.set_state(SessionNameStates.awaiting_name)
        await fsm.update_data(flow="summary", old_sid=old_sid, summary_text=text)

    async def send_blocked(self, job: TextJob) -> None:
        """Tell the user that a Full Session refused the message, in the chat it was sent to."""
        if job.chat_id is None:
            await self._bot.send_message(job.user_id, FULL_TEXT, reply_markup=options_markup())
            return
        await self._bot.send_message(
            job.chat_id,
            GROUP_FULL_TEXT,
            reply_parameters=_reply(job.reply_to),
            reply_markup=clear_markup(),
        )
