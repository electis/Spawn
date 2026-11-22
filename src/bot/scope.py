"""Owner keys for Session_Store and job queues.

Private chats keep the User id as the key, so existing data stays valid.
A group has its own Sessions for each member, so a group key combines the group and the User.
Private chat ids are positive and group chat ids are negative, so the two never collide.
"""
from __future__ import annotations

GROUP_TYPES = frozenset({"group", "supergroup"})

USER_ID_BITS = 40  # Telegram User ids are far below 2**40


def store_key(chat_id: int, user_id: int) -> int:
    """Return the owner key for a message from user_id in chat_id."""
    if chat_id == user_id:
        return user_id
    if not 0 < user_id < 1 << USER_ID_BITS:
        raise ValueError(f"user id out of range: {user_id}")
    return -((abs(chat_id) << USER_ID_BITS) | user_id)
