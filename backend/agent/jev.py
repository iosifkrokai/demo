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

Degradation
    This client raises, but it never decides what an outage means: `JevError`
    (no key / unreachable) is the *recoverable* class, and the two callers
    degrade on it (deterministic intent, retrieval order) instead of failing
    the request.  A 200 with an unusable body stays a plain ValueError — that
    is a contract break to be fixed loudly, not a degraded upstream.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from . import constants
from .config import settings

OPENROUTER_SYSTEMONE_URL = "https://openrouter.ai/api/v1/systemone"


class JevError(RuntimeError):
    """Recoverable Jev failure — degrade, do not fail the request.

    RuntimeError subclass on purpose: callers that only knew the old
    `RuntimeError("… Jev unavailable")` keep working unchanged.
    """


class JevUnavailableError(JevError):
    """No OPENROUTER_API_KEY — no request can be sent at all."""


class JevUpstreamError(JevError):
    """OpenRouter is set up but did not answer: timeout, 4xx/5xx, DNS."""


def api_key() -> str | None:
    """The OpenRouter key for this process, or None.

    Read from the environment on every call (the live source of truth) with the
    import-time settings snapshot as a fallback.  Both OpenRouter clients go
    through here — the Jev decisions below and the embeddings client in
    planner/pipeline.py — so "is OpenRouter configured?" has exactly one
    answer in the process, and /health can report it honestly.
    """
    return os.environ.get("OPENROUTER_API_KEY") or settings.OPENROUTER_API_KEY


def available() -> bool:
    """True when a request can actually be sent to OpenRouter.

    Cheap and offline: it answers "do we hold a key", not "is OpenRouter up".
    An upstream that accepts the key and then times out is handled by the
    caller catching JevUpstreamError.
    """
    return bool(api_key())


def ask(
    state: str | dict | list,
    questions: dict[str, dict[str, Any]],
    *,
    model: str = constants.JEV_MODEL,
    timeout_s: float = constants.JEV_TIMEOUT_S,
) -> dict[str, dict[str, Any]]:
    """POST /systemone. Returns {question_id: answer_dict}.

    Raises JevUnavailableError without a key, JevUpstreamError on any
    transport or HTTP failure. Callers that can produce a useful answer
    without the model (planner/intent.py, planner/rerank.py) catch JevError
    and degrade; a response that is not shaped like an answers map raises
    ValueError.
    """
    key = api_key()
    if not key:
        raise JevUnavailableError("OPENROUTER_API_KEY not set — Jev unavailable")

    try:
        with httpx.Client(timeout=timeout_s) as client:
            r = client.post(
                OPENROUTER_SYSTEMONE_URL,
                headers={
                    "Authorization": f"Bearer {key}",
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
    except httpx.HTTPError as exc:
        # Timeouts, connect/read errors and 4xx/5xx statuses all mean "no
        # answer right now" — the same thing to a caller that can degrade.
        raise JevUpstreamError(f"jev: /systemone failed: {exc}") from exc
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
