"""The notes spliced into the model's instructions.

Three pieces: the territories the system already knows, the controls the user
pressed, and the facts about the request the model cannot read from the text.
"""

from __future__ import annotations

from agent.models import ReaderBrief


def _known_areas_note() -> str:
    """The territories the system already knows, as data for the model.

    The slugs listed here are what `find_areas` resolves to and what `areas` must carry.
    """
    from reference import areas as areas_mod

    lines = []
    for slug, entry in sorted(areas_mod.load_areas().items()):
        kind = entry.get("kind") or "area"
        name_ru = entry.get("name_ru") or slug
        name_en = entry.get("name_en") or ""
        lines.append(f"{slug} ({kind}: {name_ru}" + (f" / {name_en}" if name_en else "") + ")")
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
    fixed.append(f"result_mode={brief.result_mode}")
    if not fixed:
        return ""
    return (
        "The user already set these with visible controls; they are applied "
        "with higher precedence than your reading, so restate only what the "
        "text adds: " + ", ".join(fixed) + ".\n"
    )


def _request_note(brief: ReaderBrief) -> str:
    """Facts the model needs and cannot find in the request text.

    These are facts about the request, not asks: the instructions say not to restate them.
    """
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


# The blocks compose_instructions assembles; the leading underscore is the
# package-internal convention, not a privacy boundary.
__all__ = ["_known_areas_note", "_request_note", "_ui_note"]
