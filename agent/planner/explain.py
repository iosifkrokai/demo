"""Step 9 — Template-based explanation.

No LLM — assemble the trace into a short Russian description. The webapp
surfaces this in the route summary; clients can hide it for compact view.

The explanation is purely deterministic and reflects exactly what the
pipeline did (which categories, what algorithm, any auto-relax). This
keeps the per-request cost at zero and ensures the user sees the truth.
"""

from __future__ import annotations

from ..models import Candidate


def explain(route: list[Candidate], trace: dict, walk_seconds: float) -> str:
    if not route:
        return "Маршрут не удалось построить."

    n = len(route)
    walk_min = max(1, int(walk_seconds // 60))
    parts: list[str] = [
        f"Пеший маршрут по Гродно: {n} остановок, ≈{walk_min} мин ходьбы."
    ]

    cats = [c.category for c in route if c.category]
    unique_cats = sorted(set(cats))
    if unique_cats:
        parts.append("Категории: " + ", ".join(unique_cats) + ".")

    parts.append("")
    parts.append("Маршрут:")
    for i, c in enumerate(route, 1):
        cat_part = f" ({c.category})" if c.category else ""
        line = f"{i}. {c.name}{cat_part}"
        if c.blurb:
            line += f" — {c.blurb[:120]}"
        parts.append(line)

    algo = trace.get("algorithm")
    if algo and algo not in ("direct",):
        parts.append("")
        parts.append(f"_Алгоритм построения: {algo}.")

    if not trace.get("fits_budget", True):
        parts.append("⚠ Маршрут слегка выходит за заявленный бюджет.")

    div = trace.get("diversity", 1.0)
    if n >= 3 and div < 0.4:
        parts.append("⚠ Места похожи по категориям — маршрут однообразный.")

    return "\n".join(parts)
