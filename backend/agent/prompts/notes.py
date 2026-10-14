"""The notes spliced into the model's instructions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agent.models import ReaderBrief


def _known_areas_note(areas: Sequence[Any]) -> str:
    """The territories the system already knows, as data for the model."""

    lines = []
    for area in sorted(areas, key=lambda a: a.code):
        kind = area.kind or "area"
        name_ru = area.name_ru or area.code
        name_en = area.name_en or ""
        lines.append(f"{area.code} ({kind}: {name_ru}" + (f" / {name_en}" if name_en else "") + ")")
    return "; ".join(lines)


def _ui_note(brief: ReaderBrief) -> str:
    """Tell the model which controls the user already set — it must not fight them."""
    fixed: list[str] = []
    if brief.party_adults is not None:
        fixed.append(f"adults={brief.party_adults}")
    if brief.party_children is not None:
        fixed.append(f"children={brief.party_children}")
    if brief.party_children_ages:
        fixed.append(f"children_ages={brief.party_children_ages}")
    if brief.mobility:
        fixed.append(f"mobility={brief.mobility}")
    if brief.time_budget_minutes not in (None, 0):
        fixed.append(f"budget_minutes={brief.time_budget_minutes}")
    if brief.hard_services:
        fixed.append(f"mandatory_services={brief.hard_services}")
    if brief.interests:
        fixed.append(f"interests={brief.interests}")
    if brief.avoid:
        fixed.append(f"avoid={brief.avoid}")
    if brief.result_mode:
        fixed.append(f"result_mode={brief.result_mode}")
    if not fixed:
        return ""
    return (
        "The user already set these with visible controls; they are applied "
        "with higher precedence than your reading, so restate only what the "
        "text adds: " + ", ".join(fixed) + ".\n"
    )


def _request_note(brief: ReaderBrief) -> str:
    """Facts the model needs and cannot find in the request text."""
    lines = [f"transport={brief.profile or 'unset'}"]
    if brief.origin is not None:
        lines.append(f"tourist_position={brief.origin[0]},{brief.origin[1]}")
    else:
        lines.append("tourist_position=unknown")
    lines.append(f"round_trip={bool(brief.round_trip)}")

    ctx = brief.context
    if ctx is not None:
        lines.append(f"refinement_instruction={ctx.instruction!r}")
        lines.append(f"revision={ctx.revision}")
        if ctx.excluded_ids:
            lines.append(f"deleted_stop_ids={ctx.excluded_ids}")
        if ctx.base_points:
            shown = "; ".join(
                f"{p.place_id if p.place_id is not None else '—'}:{p.name}"
                + ("(pinned)" if p.pinned else "")
                for p in ctx.base_points[:30]
            )
            lines.append(f"current_route=[{shown}]")
    return "\n".join(lines)


__all__ = ["_known_areas_note", "_request_note", "_ui_note"]
