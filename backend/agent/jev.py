"""TypeSafe Jev client — System One structured decisions via OpenRouter.

Jev does not generate text. You send a state plus typed questions and get
typed answers (yes/no probability, a choice from a closed set, or a score
on a rubric) — no JSON parsing, no hallucinated keys.

OpenRouter exposes it through the Decisions API (NOT /chat/completions —
the model page has hasCompletions=false):
    POST https://openrouter.ai/api/v1/systemone
    {"state": ..., "model": "typesafe/jev-1.13", "questions": {...}}

Question types (docs.typesafe.ai):
    noul   — yes/no, answer.noul ∈ [0,1] (probability of yes)
    choice — pick from criteria options, answer.choice + probabilities
    score  — rate on ordered criteria levels, answer.score (weighted)

Where it fits this agent:
    * intent classification (planner/intent.py) — categories/era/party as
      typed choices instead of a Gemini JSON blob
    * rerank scoring (planner/rerank.py) — Score per (query, place) pair,
      batched into ONE call (their "parallel questions" pattern)
"""

from __future__ import annotations

from typing import Any

import httpx

from . import constants
from .config import settings

OPENROUTER_SYSTEMONE_URL = "https://openrouter.ai/api/v1/systemone"


def ask(
    state: str | dict | list,
    questions: dict[str, dict[str, Any]],
    *,
    model: str = constants.JEV_MODEL,
    timeout_s: float = constants.JEV_TIMEOUT_S,
) -> dict[str, dict[str, Any]]:
    """POST /systemone. Returns {question_id: answer_dict} — raises on failure.

    No fallbacks by design: callers decide what an upstream failure means.
    """
    api_key = settings.OPENROUTER_API_KEY
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set — Jev unavailable")

    with httpx.Client(timeout=timeout_s) as client:
        r = client.post(
            OPENROUTER_SYSTEMONE_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "state": state,
                "model": model,
                "questions": questions,
            },
        )
        r.raise_for_status()
        body = r.json()
    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise ValueError(f"jev: malformed response, no answers map: {body!r:.200}")
    return answers


# ── Typed answer readers (fail loud on shape mismatch) ──────────────────────

def noul(answer: dict) -> float:
    """Extract a yes/no probability from a noul answer."""
    v = answer.get("noul")
    if not isinstance(v, (int, float)):
        raise ValueError(f"jev: bad noul answer: {answer!r:.120}")
    return float(v)


def choice(answer: dict) -> str:
    """Extract the chosen option from a choice answer."""
    v = answer.get("choice")
    if not isinstance(v, str) or not v:
        raise ValueError(f"jev: bad choice answer: {answer!r:.120}")
    return v


def choice_probs(answer: dict) -> dict[str, float]:
    """Full probability distribution from a choice answer."""
    p = answer.get("probabilities")
    if not isinstance(p, dict):
        raise ValueError(f"jev: bad choice probabilities: {answer!r:.120}")
    return {k: float(v) for k, v in p.items()}


def score(answer: dict) -> float:
    """Extract the probability-weighted score from a score answer."""
    v = answer.get("score")
    if not isinstance(v, (int, float)):
        raise ValueError(f"jev: bad score answer: {answer!r:.120}")
    return float(v)
