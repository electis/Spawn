"""Summary prompt (Requirement 10.2; Assumption A-9).

The prompt goes to kiro-cli through stdin, like any user text. Its wording is the one
used in the T-7 check on amster, adjusted for Russian replies. The text lives on the
character (Persona.summary_prompt); SUMMARY_PROMPT is the default character's value.
"""

from bot.persona import DEFAULT_PERSONA

SUMMARY_PROMPT = DEFAULT_PERSONA.summary_prompt
