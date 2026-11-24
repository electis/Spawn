"""Replies sent instead of an error when the model refuses to answer (Requirement 2.4).

The replies belong to the character (Persona.refusal_replies). One is picked at random.
The Session_Store is not changed in that case.
"""

from bot.persona import DEFAULT_PERSONA

REFUSAL_REPLIES: tuple[str, ...] = DEFAULT_PERSONA.refusal_replies
