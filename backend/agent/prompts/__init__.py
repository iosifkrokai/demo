"""Prompt composition for the interpretation agent."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agent.prompts import rules


def compose_instructions(categories: Sequence[Any], areas: Sequence[Any], ui_note: str) -> str:
    """The system instructions handed to the model: the base rules plus the
    per-request UI note.
    """
    return rules.base_rules(categories, areas) + ui_note
