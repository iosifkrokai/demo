"""Live smoke check: the three ordinary queries the product owner reported."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

BASE_URL = os.environ.get("SMOKE_BASE_URL", "http://localhost:8080")
BUDGET_S = float(os.environ.get("SMOKE_BUDGET_S", "75"))

QUERIES: list[tuple[str, dict]] = [
    (
        "Погулять по старому Гродно с двумя детьми, туалет по пути, на два часа",
        {"time_budget_minutes": 120, "party_children": 2},
    ),
    ("замки Гродненской области", {}),
    ("где поесть в Гродно", {}),
]


def _post(body: dict, timeout: float) -> tuple[int | None, dict | None, str | None, float]:
    req = urllib.request.Request(
        f"{BASE_URL}/routes/generate",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode()), None, time.monotonic() - t0
    except urllib.error.HTTPError as exc:
        return exc.code, None, exc.read().decode()[:200], time.monotonic() - t0
    except Exception as exc:
        return None, None, f"{type(exc).__name__}: {exc}", time.monotonic() - t0


def _live() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE_URL}/health", timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def _reader() -> bool:
    """A stack without a key refuses to plan, so these checks cannot run."""
    try:
        with urllib.request.urlopen(f"{BASE_URL}/health", timeout=3) as resp:
            return bool(json.loads(resp.read().decode()).get("llm"))
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _live() or not _reader(),
    reason=f"no live backend with a query reader at {BASE_URL}",
)


@pytest.mark.parametrize("query,extra", QUERIES, ids=["old_town_toilet", "region_castles", "eat"])
def test_smoke_query(query: str, extra: dict):
    status, body, err, elapsed = _post({"query": query, **extra}, timeout=BUDGET_S)

    assert err is None, f"{query!r} failed after {elapsed:.1f}s: {err}"
    assert status == 200, f"{query!r} -> HTTP {status}: {err}"
    assert elapsed < BUDGET_S, f"{query!r} took {elapsed:.1f}s (budget {BUDGET_S}s)"
    assert body is not None
    assert len(body["points"]) >= 2, f"{query!r} returned {len(body['points'])} stop(s)"

    interp = body.get("interpretation")
    assert interp, f"{query!r} carries no `interpretation` block"
    assert interp["source"] in ("llm", "mixed", "explicit")
    assert interp["status"] == body["status"] or interp["status"] in (
        "ready", "infeasible", "degraded", "pending",
    )
    assert interp["requirements"], "the chips list must not be empty"

    for signal in interp["unmet"]:
        assert signal["status"] != "satisfied"
        assert signal["reason"], f"unmet {signal['code'] or signal['name']} has no reason"


def test_the_mandatory_toilet_is_never_silently_dropped():
    """The owner's live bug, pinned: either the toilet is on the route, or the
    response says it could not be placed."""
    query, extra = QUERIES[0]
    status, body, err, _elapsed = _post({"query": query, **extra}, timeout=BUDGET_S)
    assert status == 200, f"HTTP {status}: {err}"

    interp = body["interpretation"]
    toilet = [
        r for r in interp["requirements"]
        if r["kind"] == "service" and r["code"] == "туалет"
    ]
    assert toilet, "the toilet the user stated must appear as a requirement"

    on_route = toilet[0]["status"] == "satisfied" and toilet[0]["place_ids"]
    reported = any(
        s["kind"] == "service" and s["code"] == "туалет" for s in interp["unmet"]
    )
    assert on_route or reported, (
        "the toilet is neither on the route nor reported in interpretation.unmet"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
