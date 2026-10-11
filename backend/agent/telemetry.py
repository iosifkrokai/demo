"""The trace spans of one interpretation run.

The model call, the tool exchanges inside it, and the tokens it spent.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent import llm
from agent.models import ReaderBrief
from agent.prompts import compose_instructions
from agent.prompts.notes import _ui_note
from agent.schema import AgentReading
from telemetry import trace

log = logging.getLogger(__name__)

_TOOL_ARGS_MAX = 300
_TOOL_ANSWER_MAX = 400


def _clip(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` characters, with the cut left visible."""
    return text if len(text) <= limit else text[:limit] + "…"


def _as_text(value: Any) -> str:
    """A tool argument or answer as one compact string: JSON for structures."""
    text: Any = value
    if isinstance(text, str):
        for _ in range(3):
            try:
                decoded = json.loads(text)
            except (TypeError, ValueError):
                break
            text = decoded
            if not isinstance(text, str):
                break
    if isinstance(text, str):
        return text
    try:
        return json.dumps(text, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(text)


def _tool_exchanges(result: Any) -> list[dict[str, Any]]:
    """What the model asked its tools, and what came back — one entry per call.

    An answer travels as its size plus its head, because it is the part that can be long.
    """
    try:
        from pydantic_ai.messages import ToolCallPart, ToolReturnPart
    except Exception:
        return []
    all_messages = getattr(result, "all_messages", None)
    if all_messages is None:
        return []
    exchanges: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for message in all_messages():
        for part in getattr(message, "parts", ()):
            if isinstance(part, ToolCallPart):
                entry: dict[str, Any] = {
                    "tool": part.tool_name,
                    "args": _clip(_as_text(part.args), _TOOL_ARGS_MAX),
                }
                exchanges.append(entry)
                if part.tool_call_id:
                    by_id[part.tool_call_id] = entry
            elif isinstance(part, ToolReturnPart):
                answered: dict[str, Any] | None = by_id.get(part.tool_call_id or "")
                if answered is None:
                    answered = {"tool": part.tool_name}
                    exchanges.append(answered)
                answer = _as_text(part.content)
                answered["answer_chars"] = len(answer)
                answered["answer"] = _clip(answer, _TOOL_ANSWER_MAX)
    return exchanges


def _token_usage(usage: Any) -> dict[str, int] | None:
    """The tokens one agent run spent, under Langfuse's own key names."""
    if usage is None:
        return None
    spent_in = int(getattr(usage, "input_tokens", 0) or 0)
    spent_out = int(getattr(usage, "output_tokens", 0) or 0)
    return {"input": spent_in, "output": spent_out, "total": spent_in + spent_out}


def _record_model_call(brief: ReaderBrief, prompt: str, result: Any) -> None:
    """One observation stands for the whole run; ``requests``/``tool_calls`` say
    how many turns there really were.
    """
    usage = getattr(result, "usage", None)
    reading = getattr(result, "output", None)
    usable = isinstance(reading, AgentReading)
    answer: Any = (
        reading.model_dump() if usable else getattr(getattr(result, "response", None), "text", None)
    )
    facts: dict[str, Any] = {"cached": False}
    if usage is not None:
        facts = {**facts, "requests": usage.requests, "tool_calls": usage.tool_calls}
    exchanges = _tool_exchanges(result)
    if exchanges:
        facts["tools"] = exchanges
    trace.record(
        "interpret · model",
        "ok" if usable else "error",
        input=[
            {"role": "system", "content": compose_instructions(_ui_note(brief))},
            {"role": "user", "content": prompt},
        ],
        output=answer,
        kind="generation",
        model=llm.model_name(),
        usage=_token_usage(usage),
        **facts,
    )


# agent.runner records the model call through this entry point.
__all__ = ["_record_model_call"]
