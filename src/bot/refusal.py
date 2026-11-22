"""Replies sent instead of an error when the model refuses to answer (Requirement 2.4).

One of these is picked at random. The Session_Store is not changed in that case.
"""

REFUSAL_REPLIES: tuple[str, ...] = (
    "Тут я не пойду. Есть дороги, по которым не ходят, как бы ни просили.",
    "Этот путь мне закрыт. Спроси о другом, и я отвечу честно.",
    "Не стану помогать с этим. Если нужно что-то иное, говори.",
)