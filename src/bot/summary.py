"""Fixed Summary_Prompt (Requirement 10.2; Assumption A-9).

The prompt goes to kiro-cli through stdin, like any user text. Its wording is the one
used in the T-7 check on amster, adjusted for Russian replies.
"""

SUMMARY_PROMPT = (
    "Сделай краткую выжимку нашей беседы для продолжения в новой сессии. "
    "Перечисли ключевые факты, принятые решения, открытые задачи и предпочтения "
    "пользователя. Отвечай только текстом выжимки, без вступления."
)
