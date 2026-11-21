"""Replies sent instead of an error when the model refuses to answer (Requirement 2.4).

One of these is picked at random. The Session_Store is not changed in that case.
"""

REFUSAL_REPLIES: tuple[str, ...] = (
    "Тему обсудим, инструкции не дам. Скажи, что тебе нужно на самом деле, и я помогу с этим.",
    "Конкретного порядка действий я не назову, но о самой теме поговорить могу. Спроси иначе, и я отвечу.",
    "В таком виде я отвечать не стану. Расскажи, зачем тебе это, и я дам обзор, который можно принять.",
)