"""Prompt composition for the interpretation agent."""

from __future__ import annotations

from agent.prompts import rules


def compose_instructions(ui_note: str) -> str:
    """The system instructions handed to the model: the base rules plus the
    per-request UI note.
    """
    return rules.base_rules() + ui_note
