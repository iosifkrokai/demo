"""Step 9 — Template-based explanation.

No LLM — assemble the trace into a short Russian description. The webapp
surfaces this in the route summary; clients can hide it for compact view.

The explanation is purely deterministic and reflects exactly what the
pipeline did (which categories, what algorithm, any auto-relax) and, when the
verifier ran, the satisfied / unmet / uncertain breakdown of the user's own
requirements. This keeps the per-request cost at zero and ensures the user
sees the truth.
"""

from __future__ import annotations

from collections import Counter

from ..models import Candidate
from ..requirements import TripRequirements
from .verify import verify_summary


def _area_name(route: list[Candidate]) -> str:
    """Derive a human-readable area name from the route's stops.

    Prefer district over town when all stops are in the same district
    (e.g. "Лидский район" is more informative than "Лида").
    If all stops share one town, use that.
    """
    if not route:
        return "Гродно"

    towns = [c.town for c in route if c.town]
    districts = [c.district for c in route if c.district]

    # If all stops share one district -> use the district.
    if districts and len(set(districts)) == 1:
        return districts[0]

    # Mixed area: when the stops span several districts the tour covers the
    # voblast — naming one of its towns ("по Волковыск") would be wrong.
    if districts and len(set(districts)) > 1:
        return "Гродненской области"

    # If all stops share one town -> use the town.
    if towns and len(set(towns)) == 1:
        return towns[0]

    # Mixed area: take the most common town or fall back to "Гродно".
    if towns:
        most_common = Counter(towns).most_common(1)[0][0]
        if most_common:
            return most_common

    return "Гродно"


def _requirements_section(
    trace: dict, requirements: TripRequirements | None
) -> list[str]:
    """The satisfied / unmet / uncertain breakdown, from the verifier.

    Prefers the live ``requirements`` object when one is passed (statuses were
    written by ``verify``), else the snapshot ``validate`` stored in the trace.
    Returns an empty list when the verifier never ran, so callers that do not
    use requirements get byte-for-byte the old explanation.
    """
    if requirements is not None and requirements.requirements:
        summary = verify_summary(requirements)
    else:
        summary = trace.get("requirement_summary")
    if not summary:
        return []

    lines: list[str] = []
    labels = (
        ("satisfied", "✔", "выполнено"),
        ("unmet", "✘", "не выполнено"),
        ("uncertain", "?", "неизвестно"),
    )
    for key, mark, ru in labels:
        items = summary.get(key) or []
        if items:
            lines.append(f"{mark} {ru}: {', '.join(items)}")

    status = summary.get("status")
    if status == "infeasible":
        lines.append("⚠ Обязательное условие выполнить не удалось.")
    elif status == "degraded":
        lines.append("⚠ Часть условий не подтверждена данными.")

    if not lines:
        return []
    return ["", "Условия запроса:", *lines]


def explain(
    route: list[Candidate],
    trace: dict,
    walk_seconds: float,
    costing: str = "pedestrian",
    requirements: TripRequirements | None = None,
) -> str:
    if not route:
        return "Маршрут не удалось построить."

    n = len(route)
    walk_min = max(1, int(walk_seconds // 60))
    area = _area_name(route)
    # A region-wide request is driven, not walked — saying "пешком" over 90 km
    # is nonsense.
    if costing == "pedestrian":
        head = f"Пеший маршрут по {area}: {n} остановок, ≈{walk_min} мин ходьбы."
    else:
        head = f"Маршрут на машине по {area}: {n} остановок, ≈{walk_min} мин в пути."
    parts: list[str] = [head]

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

    parts.extend(_requirements_section(trace, requirements))

    div = trace.get("diversity", 1.0)
    if n >= 3 and div < 0.4:
        parts.append("⚠ Места похожи по категориям — маршрут однообразный.")

    return "\n".join(parts)
