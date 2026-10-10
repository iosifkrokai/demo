"""The notes spliced into the model's instructions.

Three pieces: the territories the system already knows, the controls the user
pressed, and the facts about the request the model cannot read from the text.
"""

from __future__ import annotations

from contracts.planner import GenerateReq


def _known_areas_note() -> str:
    """The territories the system already knows, as data for the model.

    The slugs listed here are what `find_areas` resolves to and what `areas` must carry.
    """
    from domain import areas as areas_mod

    lines = []
    for slug, entry in sorted(areas_mod.load_areas().items()):
        kind = entry.get("kind") or "area"
        name_ru = entry.get("name_ru") or slug
        name_en = entry.get("name_en") or ""
        lines.append(f"{slug} ({kind}: {name_ru}" + (f" / {name_en}" if name_en else "") + ")")
    return "; ".join(lines)


def _ui_note(req: GenerateReq) -> str:
    """Tell the model which controls the user already set — it must not fight them."""
    fixed: list[str] = []
    if req.party_adults is not None:
        fixed.append(f"adults={req.party_adults}")
    if req.party_children is not None:
        fixed.append(f"children={req.party_children}")
    if req.party_children_ages:
        fixed.append(f"children_ages={req.party_children_ages}")
    if req.mobility:
        fixed.append(f"mobility={req.mobility}")
    if req.time_budget_minutes not in (None, 0):
        fixed.append(f"budget_minutes={req.time_budget_minutes}")
    if req.hard_services:
        fixed.append(f"mandatory_services={req.hard_services}")
    if req.interests:
        fixed.append(f"interests={req.interests}")
    if req.avoid:
        fixed.append(f"avoid={req.avoid}")
    fixed.append(f"result_mode={req.result_mode}")
    if not fixed:
        return ""
    return (
        "The user already set these with visible controls; they are applied "
        "with higher precedence than your reading, so restate only what the "
        "text adds: " + ", ".join(fixed) + ".\n"
    )


def _request_note(req: GenerateReq) -> str:
    """Facts the model needs and cannot find in the request text.

    These are facts about the request, not asks: the instructions say not to restate them.
    """
    lines = [f"transport={req.profile or 'unset'}"]
    if req.origin is not None:
        lines.append(f"tourist_position={req.origin.lat},{req.origin.lon}")
    else:
        lines.append("tourist_position=unknown")
    lines.append(f"round_trip={bool(req.round_trip)}")

    ctx = req.context
    if ctx is not None:
        lines.append(f"refinement_instruction={ctx.instruction!r}")
        lines.append(f"revision={ctx.revision}")
        if ctx.excluded_ids:
            lines.append(f"deleted_stop_ids={ctx.excluded_ids}")
        if ctx.base_points:
            shown = "; ".join(
                f"{p.id if p.id is not None else '—'}:{p.name}"
                + ("(pinned)" if p.pinned else "")
                for p in ctx.base_points[:30]
            )
            lines.append(f"current_route=[{shown}]")
    return "\n".join(lines)


# The blocks compose_instructions assembles; the leading underscore is the
# package-internal convention, not a privacy boundary.
__all__ = ["_known_areas_note", "_request_note", "_ui_note"]
