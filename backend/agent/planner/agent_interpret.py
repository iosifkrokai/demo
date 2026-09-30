"""Agentic interpretation layer — PydanticAI over OpenRouter (spec 002 §4.2).

One job: turn free text (plus the explicit UI filters) into the frozen
``TripRequirements`` contract.  The agent may call four bounded tools
(``agent/tools.py``); it may not touch the database, the network or a verdict.

What the agent decides and what it cannot
-----------------------------------------
* It decides the *meaning* of the request: which requirements exist, how strong
  they are, which categories and areas are involved, and — importantly — what
  the request asks for that this system cannot prove (those go to ``unknowns``).
* It never decides *satisfaction*.  ``_AgentReading`` has no ``status`` field at
  all: a model that returns ``"status": "satisfied"`` has that field dropped by
  validation, and every requirement this module produces stays ``pending`` until
  the deterministic verifier (spec §4.4) proves it against real data.

Precedence and provenance
-------------------------
Explicit ``GenerateReq`` filters are applied first with ``source="ui"``; text
readings that collide with a UI choice are dropped rather than merged, so a
model guess can never override a visible control.  Every text requirement keeps
the fragment of the request that produced it — and a fragment the model made up
(not actually present in the query) is discarded rather than stored as a quote.

Degradation
-----------
``interpret_with_agent`` returns ``None`` — never a half-filled contract —
whenever the agent cannot be trusted to produce a complete answer: no
``OPENROUTER_API_KEY``, PydanticAI not importable, a run that exceeds the tool
call / request / token budget, a wall-clock timeout, or any model or tool
failure.  ``None`` is the caller's licence to fall back to
``intent.build_requirements`` (the no-LLM path), which keeps every explicit UI
filter.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from .. import constants, tools
from ..config import openrouter_api_key
from ..models import GenerateReq
from ..requirements import PartyComposition, Requirement, TripRequirements
from ..taxonomy import all_categories, resolve_code

# The guarded names below are only used behind a `pydantic_ai is None` check,
# which is a flow correlation the type checker does not make.
# pyright: reportPossiblyUnboundVariable=false

# The SDK is imported once, behind a guard: this module must import (and the
# no-key path must answer) on a machine where pydantic-ai is not installed at
# all — that is the same degradation as "no model", and it is a supported state
# (spec §4.2: the agent is never the only way to understand a request).
try:  # pragma: no cover — the branch taken depends on the deployment
    import pydantic_ai
    from pydantic_ai import RunContext, UsageLimits
    from pydantic_ai.models.openrouter import OpenRouterModel
    from pydantic_ai.providers.openrouter import OpenRouterProvider
    from pydantic_ai.settings import ModelSettings

    SDK_IMPORT_ERROR: str | None = None
except ImportError as exc:  # pragma: no cover
    # Nothing but `pydantic_ai = None` is rebound: the guarded import keeps
    # the annotated names resolvable for type checkers, and every use of them
    # sits behind `pydantic_ai is None` / `available()`.
    pydantic_ai = None  # type: ignore[assignment]
    SDK_IMPORT_ERROR = str(exc)

log = logging.getLogger(__name__)

# ── Limits ──────────────────────────────────────────────────────────────────
# Hard budgets for one interpretation. They bound cost, latency and blast radius;
# exceeding any of them returns None and the request falls back to the keyword
# reader. Measured: too tight a ceiling is what starved the reading, not the model
# — a deep model (2.5-pro, sonnet-4.5) needed 11-14 s per call, so a 20 s per-request
# HTTP timeout was cutting reasoning off mid-flight and the thin reading that came
# back looked like a weak model. The ceilings are now generous enough that a
# thinking model finishes, and still finite: one interpretation may not run away.
# Re-measured 2026-09-30 against the live catalogue, and every one of these was
# raised because a model hit it: a 4096 output cap cut z-ai/glm-5.3-prime off
# BEFORE it wrote anything («Model token limit (4096) exceeded before any response
# was generated»), and upstage/solar-mini4 blew the 20 tool calls and the 8 turns
# on four of five requests, falling back to keywords. A ceiling a model reaches is
# not a guard, it is a starved run — so all of them are roomier, and still finite:
# one interpretation may not run away.
MAX_TOOL_CALLS = 24  # tool_calls_limit
MAX_REQUESTS = 12  # model turns (request_limit) — no unbounded loop
MAX_OUTPUT_TOKENS = 8192  # model_settings.max_tokens — a full requirements contract
WALL_CLOCK_TIMEOUT_S = 120.0
MODEL_TIMEOUT_S = 90.0  # per-request HTTP timeout, below the wall clock

# Model is a deployment fact, not a tuning knob (spec §4.2: the measurement
# picks the model, the framework stays).
#
# The old rationale for 2.5-pro — "flash reads two of the requests as ZERO
# requirements" — was measured BEFORE the instructions were reworked, and on
# 2026-09-30 it no longer reproduces: measured again on the app's own requests
# with a KNOWN-CORRECT answer per request (scripts/compare_interpret_models.py,
# three requests: forced hard service, forced must_visit + soft café, English
# soft service), 2.5-flash and 2.5-pro both met 6/6 expectations. Flash did it in
# 1.8-2.7 s per reading against 2.5-pro's 8.6-19.6 s, at $0.30/1M against
# $1.25/1M. Same reading, four times cheaper and several times faster — and the
# tourist is waiting on this call.
#
# Runners-up, for the record: deepseek/deepseek-v4.1-flash also met 6/6 at
# $0.30/1M but needed 2.8-5.7 s; z-ai/glm-5.3-flash met 6/6 at $0.15/1M yet took
# up to 98 s on one request, which the wall clock would cut off in production.
# cohere/command-a-plus turned a soft «a toilet on the way» into a hard one (5/6
# — it would force a detour the tourist never asked for), xiaomi/mimo-v2.6-flash
# returned nothing at all, and qwen3.8-omni-flash cannot take this app's forced
# tool_choice (400). Overridable per-process with AGENT_INTERPRET_MODEL.
DEFAULT_MODEL = "google/gemini-2.5-flash"


def _model_name() -> str:
    return os.environ.get("AGENT_INTERPRET_MODEL") or DEFAULT_MODEL


# ── The agent's output schema ───────────────────────────────────────────────
# Deliberately NOT `TripRequirements`: there is no `status`, no `place_ids`, no
# `reason`. The model fills meaning; the verifier fills verdicts.

RequirementKindL = Literal["must_visit", "service", "interest", "avoid"]
StrengthL = Literal["hard", "soft"]


class AgentRequirement(BaseModel):
    """One requirement the model read out of the text."""

    kind: RequirementKindL
    strength: StrengthL = "soft"
    # Canonical taxonomy code for service/interest/avoid; None for must_visit.
    code: str | None = None
    # Named place for must_visit.
    name: str | None = None
    # Verbatim fragment of the user's request that produced this requirement.
    text: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    # A place id returned by search_places/get_place_facts. Accepted only when
    # that id was actually observed in this run (see `_observed_ids`).
    place_id: int | None = None


class AgentReading(BaseModel):
    """Everything the agent is allowed to say about one request."""

    requirements: list[AgentRequirement] = Field(default_factory=list)
    adults: int | None = Field(default=None, ge=0, le=50)
    children: int | None = Field(default=None, ge=0, le=20)
    # Ages only when the user named them; anything else must stay absent.
    children_ages: list[int] = Field(default_factory=list)
    mobility: list[str] = Field(default_factory=list)
    budget_minutes: int | None = None
    areas: list[str] = Field(default_factory=list)
    # Asks this system cannot represent or prove ("без лестниц", "не устать").
    unknowns: list[str] = Field(default_factory=list)
    # "route" — a walk with an order and geometry; "catalogue" — the matching
    # places as a list, no order and no line. Only the model can tell an
    # exhaustive request («все костёлы Гродненской области») from a walk, and
    # without this field the pipeline could only ever mirror the client's own
    # `result_mode`, which is why the golden's catalogue check was unverifiable.
    # None means "no opinion": the request's value stands.
    result_mode: Literal["route", "catalogue"] | None = None
    # Names in the request that lie outside the region this system serves
    # (Гродненская область, Belarus): a foreign city or landmark ("Вильнюс",
    # "Кафедральный собор Святого Станислава в Вильнюсе"), or a Belarusian place
    # beyond the oblast. Reading geography is the model's job; what follows from
    # it — refusing to plan a route somewhere else — is decided deterministically
    # in the planner (see `planner/outside_coverage.py`).
    outside_coverage: list[str] = Field(default_factory=list)


# ── Dependencies handed to the tools ────────────────────────────────────────


@dataclass
class InterpretDeps:
    """What the tools get: a connection, and the ids they returned.

    ``observed_ids`` is the honesty guard for `place_id`: the model may only
    reference a place the tools actually handed it, so a hallucinated id cannot
    enter the contract.
    """

    db: Any | None = None
    observed_ids: set[int] = field(default_factory=set)


# ── Agent construction ──────────────────────────────────────────────────────


def _known_areas_note() -> str:
    """The territories the system already knows, as data for the model.

    Without this the model has no way to tell a district from an unservable ask:
    asked «Старый город за два часа пешком» it offered the district as an
    unknown category, which the client renders as something the system failed to
    handle — for a request that is perfectly normal. The canonical slugs listed
    here are what `find_areas` resolves to and what `areas` must carry.
    """
    from .. import areas as areas_mod

    lines = []
    for slug, entry in sorted(areas_mod.load_areas().items()):
        kind = entry.get("kind") or "area"
        name_ru = entry.get("name_ru") or slug
        name_en = entry.get("name_en") or ""
        lines.append(f"{slug} ({kind}: {name_ru}" + (f" / {name_en}" if name_en else "") + ")")
    return "; ".join(lines)


def _instructions(ui_note: str) -> str:
    catalogue = "; ".join(f"{cat.code} ({cat.ru}/{cat.en}, {cat.role})" for cat in all_categories())
    return (
        "You interpret a tourist's walking-route request for the Grodno region "
        "(Belarus) into a structured requirement list.\n"
        "Use the tools to ground every factual claim: call search_places before "
        "naming or id-ing a place, find_areas before restricting to a territory, "
        "get_place_facts for opening hours or ticket price.\n"
        f"Tool budget: at most {MAX_TOOL_CALLS} tool calls in total. One call per "
        "distinct need, never the same query twice — then answer. If you are near "
        "the budget, answer with what you already know and put the rest in "
        "`unknowns`.\n"
        "Canonical category codes (use ONLY these, never invent one): "
        f"{catalogue}.\n"
        "Known territories (canonical slugs — `find_areas` returns these): "
        f"{_known_areas_note()}.\n"
        "Rules:\n"
        "- A requirement is a thing the user asked for, with the verbatim "
        "fragment of their text that says so (copy it exactly).\n"
        "- strength: 'hard' only when the user made it mandatory "
        "('обязательно', 'must'), otherwise 'soft'.\n"
        "- The lines above the request — transport, tourist_position, round_trip, "
        "and on a refinement turn the current route, the deleted ids and the "
        "instruction — are given to you, not asked for: never restate them as "
        "requirements. On a refinement turn keep every stop marked (pinned), never "
        "bring back a stop listed in deleted_stop_ids, and read the new text as a "
        "delta against that route.\n"
        "- A named place the user wants to SEE (a landmark, a museum, one "
        "specific church, 'Мирский замок') is a `must_visit`: call search_places "
        "and give its name and place_id.\n"
        "- A named TERRITORY is the scope of the walk, not a stop: a district, a "
        "quarter, a town, the oblast ('Старый город', 'Коложа', 'Слоним', "
        "'Гродненская область'). Call find_areas and put the canonical slug it "
        "returns into `areas`. A territory is never a `must_visit` and never an "
        "`unknown` — the plan already follows it. A request may therefore "
        "legitimately produce ZERO requirements when the user only named a "
        "territory and a duration: that is a complete answer, not a failure.\n"
        "- Anything the user asked for that has no canonical code, is not a "
        "territory, and that this system cannot prove (step-free access, opening "
        "hours not in the data, a service you could not confirm) goes into "
        "`unknowns`, worded as the user's own ask. Never put a place or a "
        "territory name here just because it has no category code.\n"
        "- `result_mode`: set \"catalogue\" when the user asks for EVERY one of a "
        "category across a territory («все костёлы Гродненской области», «все "
        "замки области»): that is a list to choose from, not a walk, and a "
        "pedestrian tour over 200 km is not an answer to it. Leave it unset for "
        "a request about walking between chosen places — the default \"route\" "
        "then stands.\n"
        "- Never decide whether a requirement is satisfied: that is not your job "
        "and there is no field for it.\n"
        "- Never invent ages: report children_ages only for ages the user "
        "actually named.\n"
        "- `outside_coverage` lists the names in the request that are NOT inside "
        "the served region (Гродненская область, Belarus) — a foreign city or "
        "landmark, or a Belarusian place beyond the oblast. Copy each name "
        "verbatim. Leave it empty when everything named is inside the region. "
        "This is a fact about geography, not a decision: never refuse a request "
        "yourself, and never guess an object into the list because it looked "
        "absent from the data you saw.\n"
        "- User language: label text in the request's own locale.\n"
        # The examples below must stay OUT of the golden set, the benchmark and
        # the tests: wording taken from those files teaches the model the answers
        # instead of the rule, and the suite then passes for the wrong reason.
        # They are concrete on purpose (a placeholder teaches a weaker lesson),
        # but every phrase here was checked against benchmarks/, tests/ and
        # scripts/ before it was written in.
        "Worked shapes (the wording is yours; the shape is the point):\n"
        "- «Погуляю по старому городу два часа» → areas=[grodno-old-town], "
        "requirements=[] — a district and a duration, nothing to visit listed.\n"
        "- «Хочу монастыри и костёлы Лиды» → requirements=[interest монастырь "
        "(soft), interest костёл (soft)], areas=[lida-district]; the town itself "
        "needs no must_visit.\n"
        "- «перекусить недалеко, уборная — обязательно» → requirements=[service "
        "кафе (soft), service туалет (hard)].\n"
        "- «Любчанский замок и Новогрудок пешком» → must_visit Любчанский замок "
        "(resolved via search_places), areas=[novogrudok-district] — a named "
        "object is a stop, a named town is the scope.\n"
        "- «все монастыри по области» → result_mode=catalogue, "
        "requirements=[interest монастырь (soft)], areas=[grodno-oblast] — a "
        "list, not a 200 km walk.\n"
        f"{ui_note}"
    )


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


def _make_model() -> Any:
    """Build the OpenRouter model. Tests replace this with a fake model."""
    key = openrouter_api_key()
    if not key:
        raise RuntimeError("no OPENROUTER_API_KEY")  # pragma: no cover — checked earlier
    return OpenRouterModel(_model_name(), provider=OpenRouterProvider(api_key=key))


def _request_note(req: GenerateReq) -> str:
    """Facts the model needs and cannot find in the request text.

    Measured gap: the reading saw only `locale` and the raw sentence, while the
    contract it fills carries — and the pipeline acts on — the tourist's own
    position, the chosen transport, whether the walk returns to its start, and the
    whole refinement state (the route as it stands, the stops the user deleted by
    hand, and the delta instruction). Asked «убери музей» on a refinement turn the
    model was blind to the route it was editing. These are facts about the
    request, not asks: the instructions say not to restate them.
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


def _build_agent(model: Any, req: GenerateReq) -> Any:
    """Create the PydanticAI agent and register the bounded tool surface."""
    agent = pydantic_ai.Agent(
        model,
        output_type=AgentReading,
        deps_type=InterpretDeps,
        instructions=_instructions(_ui_note(req)),
    )

    def _remember(ctx: Any, out: dict) -> dict:
        """Record the ids a tool actually returned (the place_id guard)."""
        for row in out.get("results") or ():
            pid = row.get("id") if isinstance(row, dict) else None
            if isinstance(pid, int):
                ctx.deps.observed_ids.add(pid)
        return out

    @agent.tool
    def find_areas(ctx: RunContext[InterpretDeps], term: str, locale: str = "ru") -> dict:
        """Resolve a territory name to canonical area codes for this region."""
        return tools.find_areas_with_db(ctx.deps.db, term, locale)

    @agent.tool
    def search_places(
        ctx: RunContext[InterpretDeps],
        query: str,
        category_codes: list[str] | None = None,
        limit: int = tools.DEFAULT_SEARCH_LIMIT,
        near_lat: float | None = None,
        near_lon: float | None = None,
        radius_m: float | None = None,
    ) -> dict:
        """Search real places by text and/or canonical category codes (capped)."""
        return _remember(
            ctx,
            tools.search_places_with_db(
                ctx.deps.db, query, category_codes, limit, near_lat, near_lon, radius_m
            ),
        )

    @agent.tool
    def get_place_facts(ctx: RunContext[InterpretDeps], place_id: int) -> dict:
        """Raw stored facts (hours, price, town, source URL) about one place."""
        return _remember(ctx, tools.get_place_facts_with_db(ctx.deps.db, place_id))

    @agent.tool
    def services_near_route(
        ctx: RunContext[InterpretDeps],
        category_codes: list[str] | None = None,
        shape: list | None = None,
        max_detour_minutes: float | None = None,
    ) -> dict:
        """Services within a detour of a route shape (NOT implemented — honest gap)."""
        return tools.services_near_route(category_codes, shape, max_detour_minutes)

    return agent


def _run_with_timeout(fn: Any, timeout_s: float) -> tuple[Any, str | None]:
    """Run `fn()` with a wall-clock bound. Returns (result, failure_reason).

    The thread cannot be killed, so a call that outlives its budget leaks until
    the HTTP timeout fires; that is why the model timeout is set below the wall
    clock. What matters here is that the *request* is not held hostage.
    """
    ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-interpret")
    try:
        future = ex.submit(fn)
        try:
            return future.result(timeout=timeout_s), None
        except FuturesTimeout:
            future.cancel()
            return None, "timeout"
        except Exception as exc:
            log.warning("agent_interpret: run failed: %s: %s", type(exc).__name__, exc)
            return None, type(exc).__name__
    finally:
        ex.shutdown(wait=False)


def _run_agent(
    query: str, req: GenerateReq, deps: InterpretDeps, wall_clock_s: float | None = None
) -> tuple[AgentReading | None, str | None]:
    """Run one bounded interpretation. Returns (reading, failure_reason)."""
    if pydantic_ai is None:  # guarded import failed at module load
        log.info(
            "agent_interpret: pydantic-ai not importable (%s) — deterministic path",
            SDK_IMPORT_ERROR,
        )
        return None, "pydantic_ai_unavailable"

    try:
        model = _make_model()
    except Exception as exc:
        log.info("agent_interpret: model unavailable: %s", exc)
        return None, "model_unavailable"

    usage_limits = UsageLimits(
        request_limit=MAX_REQUESTS,
        tool_calls_limit=MAX_TOOL_CALLS,
    )
    model_settings = ModelSettings(max_tokens=MAX_OUTPUT_TOKENS, timeout=MODEL_TIMEOUT_S)
    prompt = (
        f"locale={req.locale}\n{_request_note(req)}\nrequest={query!r}\n"
        "Return the requirement list for this request."
    )

    def call() -> Any:
        agent = _build_agent(model, req)
        return agent.run_sync(
            prompt, deps=deps, usage_limits=usage_limits, model_settings=model_settings
        )

    bound = WALL_CLOCK_TIMEOUT_S
    if wall_clock_s is not None and wall_clock_s > 0:
        bound = min(bound, float(wall_clock_s))
    result, failure = _run_with_timeout(call, bound)
    if failure is not None:
        return None, failure
    if result is None or not isinstance(getattr(result, "output", None), AgentReading):
        return None, "unexpected_output"
    return result.output, None


# ── Merging the reading into the contract ───────────────────────────────────


def _verbatim(fragment: str | None, query: str) -> str | None:
    """Keep a provenance fragment only if it is really from the query.

    A quote the model invented is worse than no quote at all, so a fragment
    that does not occur in the request is dropped (the requirement survives;
    only the false attribution goes).
    """
    if not isinstance(fragment, str):
        return None
    frag = " ".join(fragment.split())
    hay = " ".join(query.split()).lower()
    if not frag or frag.lower() not in hay:
        return None
    return frag


def _ui_requirements(req: GenerateReq) -> list[Requirement]:
    """Requirements the tourist set with a visible control (source="ui")."""
    out: list[Requirement] = []
    for code in req.hard_services:
        out.append(Requirement(kind="service", strength="hard", code=code, label=code, source="ui"))
    for code in req.interests:
        out.append(
            Requirement(kind="interest", strength="soft", code=code, label=code, source="ui")
        )
    for code in req.avoid:
        out.append(Requirement(kind="avoid", strength="hard", code=code, label=code, source="ui"))
    return out


def _ui_used(req: GenerateReq) -> bool:
    return bool(
        req.hard_services
        or req.interests
        or req.avoid
        or req.party_adults is not None
        or req.party_children is not None
        or bool(req.party_children_ages)
        or bool(req.mobility)
        or req.time_budget_minutes not in (None, 0)
    )


def _reading_requirements(
    reading: AgentReading, query: str, observed: set[int]
) -> tuple[list[Requirement], list[str]]:
    """Agent requirements → contract requirements, plus the asks we had to drop.

    A **territory is not a stop**. Asked «Гродно за два часа», the model reads
    «visit Grodno» and offers a must-visit named «Гродно» — a place that does not
    exist in the data, so the verifier could only ever report it unmet. That one
    phantom requirement appeared in almost every answer and made honest reporting
    look broken. A territory is simply not made into a requirement; it is not
    recorded as an area either, because ``areas`` holds only the *sub-areas* the
    contract knows (see `intent._areas_from_text`), and the city scope already
    follows from the query text and the region geo-fence.
    """
    from .. import areas as areas_mod

    out: list[Requirement] = []
    unsupported: list[str] = []
    for item in reading.requirements:
        code = None
        if item.kind != "must_visit":
            code = resolve_code(item.code or item.name or "")
            if code is None:
                # A code with no entry in the taxonomy is a meaning this system
                # cannot represent: report it instead of inventing a category.
                ask = (item.code or item.name or item.text or item.kind).strip()
                unsupported.append(ask)
                continue
        elif not (item.name or "").strip():
            unsupported.append(item.kind)
            continue
        elif (
            isinstance(item.place_id, int) and item.place_id in observed
        ) is False and areas_mod.resolve_area((item.name or "").strip()):
            # A city or an oblast names where to look, not what to visit.
            log.info(
                "interpretation: must_visit %r — территория (%s), не остановка",
                item.name, areas_mod.resolve_area((item.name or "").strip()),
            )
            continue

        place_id = None
        if (
            item.kind == "must_visit"
            and isinstance(item.place_id, int)
            and item.place_id in observed
        ):
            place_id = item.place_id
        out.append(
            Requirement(
                kind=item.kind,
                strength=item.strength,
                code=code,
                label=code or item.name,
                name=item.name if item.kind == "must_visit" else None,
                place_id=place_id,
                text=_verbatim(item.text, query),
                source="text",
                confidence=max(0.0, min(1.0, float(item.confidence))),
                # status stays "pending": the verifier decides, not the model.
            )
        )
    return out, unsupported


def _party(req: GenerateReq, reading: AgentReading, query: str) -> PartyComposition:
    """Party composition: explicit values win, ages are never invented."""
    adults = req.party_adults if req.party_adults is not None else reading.adults
    children = req.party_children if req.party_children is not None else reading.children
    if req.party_children_ages:
        ages = list(req.party_children_ages)
    else:
        # An age the request does not mention is invented, whatever the model
        # says — so only ages literally present in the text survive.
        ages = [a for a in reading.children_ages if str(a) in query]
    mobility: list[str] = []
    for code in list(req.mobility) + list(reading.mobility):
        if isinstance(code, str) and code and code not in mobility:
            mobility.append(code)
    return PartyComposition(adults=adults, children=children, children_ages=ages, mobility=mobility)


def _unknowns(reading: AgentReading, unsupported: list[str], mobility: list[str]) -> list[str]:
    """What the system cannot represent or prove — named, never promised."""
    unknowns = list(reading.unknowns)
    for ask in unsupported:
        entry = f"unknown_category:{ask}"
        if entry not in unknowns:
            unknowns.append(entry)
    if "wheelchair" in mobility and "wheelchair_accessible" not in unknowns:
        unknowns.append("wheelchair_accessible")
    return unknowns


def _merge(
    query: str, req: GenerateReq, reading: AgentReading, observed: set[int]
) -> TripRequirements:
    """Build the contract: UI first (it wins), then the agent's reading."""
    ui_reqs = _ui_requirements(req)
    requirements = list(ui_reqs)
    claimed = {(r.kind, r.code or r.name) for r in ui_reqs}

    agent_reqs, unsupported = _reading_requirements(reading, query, observed)
    for r in agent_reqs:
        key = (r.kind, r.code or r.name)
        if key in claimed:
            continue  # an explicit control already says this — it wins
        claimed.add(key)
        requirements.append(r)

    party = _party(req, reading, query)

    if req.time_budget_minutes is not None:
        budget = req.time_budget_minutes or None
    else:
        budget = reading.budget_minutes
    if budget is not None:
        budget = max(constants.MIN_BUDGET_MIN, min(int(budget), constants.MAX_BUDGET_MIN))

    areas: list[str] = []
    for slug in reading.areas:
        if isinstance(slug, str) and slug.strip() and slug.strip() not in areas:
            areas.append(slug.strip())

    return TripRequirements(
        locale=req.locale,
        raw_query=query,
        party=party,
        budget_minutes=budget,
        costing=req.profile,
        origin_lat=req.origin.lat if req.origin is not None else None,
        origin_lon=req.origin.lon if req.origin is not None else None,
        areas=areas,
        outside_coverage=_outside_names(reading.outside_coverage),
        # A whole-region "every one of them" request («все костёлы Гродненской
        # области») is a list to choose from, not a walk: the pipeline builds a
        # 17-hour pedestrian tour out of it otherwise. The model may say so; the
        # client's own value stands when the model has no opinion. This is the one
        # place a model answer outranks a request field, because the request's
        # default ("route") is not an explicit choice the user made.
        result_mode=reading.result_mode or req.result_mode,
        round_trip=req.round_trip,
        requirements=requirements,
        unknowns=_unknowns(reading, unsupported, party.mobility),
        source="mixed" if _ui_used(req) else "llm",
    )


def _outside_names(raw: list[str]) -> list[str]:
    """Names the model placed outside the region, de-duplicated and trimmed.

    An empty string in the list would become a must-visit for nothing, and the
    same name twice would produce two identical unmet verdicts in the answer.
    """
    out: list[str] = []
    for name in raw:
        if not isinstance(name, str):
            continue
        cleaned = name.strip()
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


# ── Public entry point ──────────────────────────────────────────────────────


def available() -> bool:
    """True when this layer could run: a key, a model name, an importable SDK.

    Cheap and offline — it answers "is the agent configured", not "is
    OpenRouter up". A model that accepts the key and then fails is handled by
    the failure paths in `interpret_with_agent`.
    """
    if pydantic_ai is None:
        return False
    if not openrouter_api_key():
        return False
    return bool(_model_name())


def interpret_with_agent(
    query: str,
    req: GenerateReq,
    *,
    db: Any | None = None,
    wall_clock_s: float | None = None,
) -> TripRequirements | None:
    """Interpret one request with the tool-using agent.

    Returns a complete ``TripRequirements`` (explicit UI filters merged in with
    higher precedence, every requirement ``pending``) or ``None`` when the agent
    is unavailable or refused. ``None`` is never a half-filled contract: the
    caller falls back to the deterministic path, which keeps the UI filters.

    ``db`` is an optional caller-owned connection; without one each tool opens
    its own short-lived connection.  ``wall_clock_s`` narrows the agent's own
    bound when the caller has less of its request deadline left.
    """
    if not isinstance(query, str) or not query.strip():
        return None
    if not available():
        log.info("agent_interpret: agent unavailable (no key/model) — deterministic path")
        return None

    deps = InterpretDeps(db=db)
    try:
        reading, failure = _run_agent(query.strip(), req, deps, wall_clock_s=wall_clock_s)
    except Exception as exc:
        log.warning("agent_interpret: unexpected failure: %s: %s", type(exc).__name__, exc)
        return None
    if reading is None:
        log.info("agent_interpret: no usable answer (%s) — deterministic path", failure)
        return None
    return _merge(query.strip(), req, reading, deps.observed_ids)
