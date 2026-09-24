"""LLM re-ranking of a built route.

The route builder is deterministic: TF-IDF picks candidates, a greedy + 2-opt
loop picks the order. That is fast and cheap, but it optimises text similarity
and walking distance, not what the user actually asked for. "музеи и история,
но без церквей" still yields two churches, because a negative constraint never
reaches the TF-IDF scorer.

This module inserts one LLM call between building and returning the route. The
model does not rewrite anything: it proposes operations (prefer / drop / swap)
over ids we handed it, and every proposal is validated here. A malformed reply
therefore costs nothing — the deterministic route is returned untouched.
"""

from __future__ import annotations

import json
import time

from agent import config, llm

SYSTEM_PROMPT = """Ты проверяешь построенный пешеходный маршрут по Гродно и можешь его поправить.

Доступные действия (только над id из списка):
- prefer: <id> — поставить точку первой в порядке обхода (когда она важнее других).
- drop: <id> — убрать точку (не соответствует запросу или не влезает в бюджет времени).
- swap: <from_id> -> <to_id> — заменить точку маршрута на другую из candidate_points.

Отвечай строго JSON без пояснений:
{"ops": ["prefer:5", "drop:12", "swap:7->3"], "notes": "<одно короткое предложение по-русски>"}

Правила:
- Используй только id из "points" и "candidate_points"; неизвестный id будет проигнорирован.
- Действий не больше 4. Если маршрут уже соответствует запросу — верни "ops": [].
- Не убирай точки без причины: на маршруте должно остаться минимум 2 точки.
- Сверяйся с "walk_minutes" и "time_budget_minutes": если бюджет превышен, сначала "drop".
- Оценивай смысл названия и описания, а не совпадение слов; отвергай явные ограничения запроса.
"""


def _brief(p: dict, extra_keys: tuple[str, ...]) -> dict:
    """Project a point down to the fields the model needs in order to judge it."""
    out = {
        "id": p.get("id"),
        "name": (p.get("name") or "")[:70],
        "category": p.get("category"),
        "description": (p.get("description") or "")[:180],
        "fun_fact": (p.get("fun_fact") or "")[:120],
    }
    for key in extra_keys:
        if key in p:
            out[key] = p[key]
    return out


def _parse_ops(parsed: dict, route_ids: set[int], candidate_ids: set[int]) -> list[tuple]:
    """Validate raw operation strings, dropping anything referencing unknown ids."""
    ops: list[tuple] = []
    for raw in parsed.get("ops", []):
        if not isinstance(raw, str) or ":" not in raw:
            continue
        kind, _, value = raw.partition(":")
        kind, value = kind.strip().lower(), value.strip()
        try:
            if kind == "prefer":
                pid = int(value)
                if pid in route_ids:
                    ops.append(("prefer", pid))
            elif kind == "drop":
                pid = int(value)
                if pid in route_ids:
                    ops.append(("drop", pid))
            elif kind == "swap" and "->" in value:
                left, _, right = value.partition("->")
                src, dst = int(left.strip()), int(right.strip())
                if src in route_ids and dst in candidate_ids:
                    ops.append(("swap", src, dst))
        except ValueError:
            continue
    return ops[: 4]  # hardcap: never accept more than 4 ops


def _apply(ops: list[tuple], points: list[dict],
           candidates: list[dict]) -> tuple[list[int], list[str]]:
    """Replay validated operations over the id order, reporting what really landed."""
    ids = [p["id"] for p in points]
    by_id = {p["id"]: p for p in points}
    cand_by_id = {p["id"]: p for p in candidates}
    applied: list[str] = []

    for op in ops:
        if op[0] == "prefer":
            pid = op[1]
            if pid in ids and ids[0] != pid:
                ids.remove(pid)
                ids.insert(0, pid)
                applied.append(f"prefer:{pid}")
        elif op[0] == "drop":
            pid = op[1]
            # Never let the model collapse the route below two stops.
            if pid in ids and len(ids) > 2:
                ids.remove(pid)
                applied.append(f"drop:{pid}")
        elif op[0] == "swap":
            src, dst = op[1], op[2]
            if src in ids and dst not in ids and dst in cand_by_id:
                ids[ids.index(src)] = dst
                by_id[dst] = cand_by_id[dst]
                applied.append(f"swap:{src}->{dst}")

    return ids, applied


def rerank(query: str, points: list[dict], candidates: list[dict],
           summary: dict, *, time_budget_minutes: int | None = None) -> dict:
    """Propose and validate re-ranking operations for an already built route.

    Returns {"ids", "applied", "notes", "model_ms", "error"}. "ids" is None when
    nothing valid came back, which tells the caller to keep the route as built.
    """
    result = {"ids": None, "applied": [], "notes": None, "model_ms": None, "error": None}
    if not config.RERANK_ENABLED or not llm.llm_is_ready() or len(points) < 2:
        return result

    payload = {
        "query": query[:400],
        "time_budget_minutes": time_budget_minutes,
        "walk_minutes": summary.get("walk_minutes"),
        "total_km": summary.get("length"),
        "points": [_brief(p, ("order",)) for p in points],
        "candidate_points": [_brief(p, ("detour_km",)) for p in candidates[: config.RERANK_POOL]],
    }

    started = time.perf_counter()
    try:
        parsed = llm.chat_json(
            SYSTEM_PROMPT,
            json.dumps(payload, ensure_ascii=False),
            model=config.RERANK_FAST_MODEL,
            timeout=config.RERANK_TIMEOUT_S,
            max_tokens=384,
        )
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        result["model_ms"] = int((time.perf_counter() - started) * 1000)
        return result
    result["model_ms"] = int((time.perf_counter() - started) * 1000)

    notes = parsed.get("notes")
    result["notes"] = notes if isinstance(notes, str) else None

    ops = _parse_ops(parsed, {p["id"] for p in points}, {p["id"] for p in candidates})
    if not ops:
        return result

    ids, applied = _apply(ops, points, candidates)
    if not applied or len(ids) < 2:
        return result

    result["ids"] = ids
    result["applied"] = applied
    return result