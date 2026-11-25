"""The /comment command in allowed groups: one summary of recent group messages.

The bot keeps the last MAX_MESSAGES text messages of each group in the bot database. The log
is filled by group updates that the bot receives (privacy mode off), and it survives restarts.
Bot messages, media, and commands are not recorded.

Argument forms:
- "10": the last 10 recorded messages.
- "2d" or "5h": the recorded messages from the last 2 days or 5 hours, at most MAX_MESSAGES
  of the most recent ones.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from bot.db import Database

MAX_MESSAGES = 100

_REQUEST = re.compile(r"^(\d+)([dh]?)$")


class CommentRequestError(ValueError):
    """The /comment argument is not a count or a period, or it is out of range."""


@dataclass(frozen=True)
class GroupMessage:
    message_id: int
    when: datetime  # timezone-aware, UTC
    author: str
    text: str


@dataclass(frozen=True)
class CommentRequest:
    count: int | None = None  # the last N messages
    period: timedelta | None = None  # messages newer than now minus this


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class GroupLog:
    """Recent text messages per group chat, kept in the bot database (table group_messages).

    Only the newest MAX_MESSAGES of each group are kept, so the log survives restarts
    without growing.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def record(self, chat_id: int, message: GroupMessage) -> None:
        sent_us = (message.when - _EPOCH) // timedelta(microseconds=1)
        conn = self._db.conn
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO group_messages "
                "(chat_id, message_id, sent_us, author, text) VALUES (?, ?, ?, ?, ?)",
                (chat_id, message.message_id, sent_us, message.author, message.text),
            )
            conn.execute(
                "DELETE FROM group_messages WHERE chat_id = ? AND message_id NOT IN ("
                "SELECT message_id FROM group_messages WHERE chat_id = ? "
                "ORDER BY sent_us DESC, message_id DESC LIMIT ?)",
                (chat_id, chat_id, MAX_MESSAGES),
            )

    def messages(self, chat_id: int) -> list[GroupMessage]:
        """The kept messages of the group, oldest first."""
        rows = self._db.conn.execute(
            "SELECT message_id, sent_us, author, text FROM group_messages "
            "WHERE chat_id = ? ORDER BY sent_us, message_id",
            (chat_id,),
        ).fetchall()
        return [
            GroupMessage(
                message_id=row["message_id"],
                when=_EPOCH + timedelta(microseconds=row["sent_us"]),
                author=row["author"],
                text=row["text"],
            )
            for row in rows
        ]


def parse_request(argument: str | None) -> CommentRequest:
    """Parse "10", "2d", or "5h". Raise CommentRequestError for anything else."""
    match = _REQUEST.match((argument or "").strip().lower())
    if match is None:
        raise CommentRequestError("format")
    value = int(match.group(1))
    unit = match.group(2)
    if value < 1:
        raise CommentRequestError("zero")
    if unit == "":
        if value > MAX_MESSAGES:
            raise CommentRequestError("too_many")
        return CommentRequest(count=value)
    if unit == "d":
        return CommentRequest(period=timedelta(days=value))
    return CommentRequest(period=timedelta(hours=value))


def select_messages(
    messages: list[GroupMessage], request: CommentRequest, now: datetime
) -> list[GroupMessage]:
    """Pick the messages the request asks for, in chronological order.

    The log holds at most MAX_MESSAGES per group, so a period never selects more than that.
    """
    if request.count is not None:
        return messages[-request.count:]
    assert request.period is not None
    cutoff = now - request.period
    return [m for m in messages if m.when >= cutoff]


def build_prompt(messages: list[GroupMessage]) -> str:
    """The text sent to Kiro: the request and the copied messages, one per line."""
    lines = "\n".join(f"{m.author}: {m.text}" for m in messages)
    return (
        "Прокомментируй обсуждение из группы ниже: коротко опиши, о чём говорили, "
        "какие мысли и решения прозвучали. Отвечай по-русски.\n\n"
        f"Сообщения группы по порядку:\n{lines}"
    )


USAGE_TEXT = (
    "Укажите число сообщений от 1 до 100, например /comment 10, "
    "или период, например /comment 2d или /comment 5h."
)
TOO_MANY_TEXT = "Можно прокомментировать не больше 100 сообщений."
EMPTY_TEXT = "В группе нет текстовых сообщений для комментария за указанный период."
