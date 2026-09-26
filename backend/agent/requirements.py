"""Frozen request-semantics contract for spec 002 (Grodno Guide rebuild).

This module is the SINGLE typed representation of "what the tourist asked for".
It is deliberately small and dependency-free: the planner, retrieval, optimizer,
verifier and the HTTP layer all speak this one vocabulary instead of each
re-deriving meaning from a query string.

Rules of the contract
---------------------
* Nothing here is a dictionary of natural-language phrases. Free-text
  understanding belongs to the interpretation stage (LLM or the deterministic
  fallback) which *fills* these structures; this module only describes them.
* Category codes are canonical domain codes (see agent/taxonomy.py). A code that
  has no data behind it must be reported as ``uncertain``, never invented.
* Every requirement carries its provenance: the raw fragment of the user's text
  or the explicit UI choice that produced it.
* Hard requirements are decided by code against real data and a real route.
  An LLM may propose the *meaning* of a requirement, never its satisfaction.

This file is owned by the integration workstream. Do not change the public
model shapes without updating docs/specs/002-grodno-guide-rebuild/plan.md.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ── Vocabulary ──────────────────────────────────────────────────────────────

# must_visit — a specific named place that must be in the route.
# service    — a facility the route must (hard) or may (soft) pass by, by
#              category code ("туалет", "кафе", ...).
# interest   — a theme the tourist wants more of ("замки", "костёлы").
# avoid      — something to keep out of the route.
RequirementKind = Literal["must_visit", "service", "interest", "avoid"]

Strength = Literal["hard", "soft"]

# pending   — extracted, not yet checked against data
# satisfied — proven by real data (a real place, on the route)
# unmet     — data exists, but the requirement could not be honoured
# uncertain — the data to decide does not exist; never present as satisfied
RequirementStatus = Literal["pending", "satisfied", "unmet", "uncertain"]

RequirementSource = Literal["text", "ui"]

ResultMode = Literal["route", "catalogue"]


class Requirement(BaseModel):
    """One thing the tourist asked for, with its provenance and its fate."""

    kind: RequirementKind
    strength: Strength = "soft"

    # Canonical domain code for service/interest/avoid ("туалет", "замок").
    # None for must_visit (which is identified by name/id instead).
    code: str | None = None

    # Human-readable label for logs and for the UI when it needs one; the
    # translatable text of the response is built from `code` + `kind`, not
    # from this string.
    label: str | None = None

    # Verbatim fragment of the user's request ("туалет по пути"), or None when
    # the requirement came from an explicit UI control.
    text: str | None = None

    # A named place the user asked for (must_visit only).
    name: str | None = None
    place_id: int | None = None

    source: RequirementSource = "text"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    status: RequirementStatus = "pending"
    # Places that prove satisfaction, and a machine-readable reason when the
    # requirement is not satisfied (localized by the API layer).
    place_ids: list[int] = Field(default_factory=list)
    reason: str | None = None

    def is_resolved(self) -> bool:
        return self.status in ("satisfied", "unmet", "uncertain")


class PartyComposition(BaseModel):
    """Who is walking. Only facts the user stated — ages are never invented."""

    adults: int | None = Field(default=None, ge=0, le=50)
    children: int | None = Field(default=None, ge=0, le=20)
    # Filled only when the user named ages; an unknown age stays absent.
    children_ages: list[int] = Field(default_factory=list)
    # e.g. ["stroller"], ["wheelchair"], ["elderly"] — free-form codes the UI
    # sends and the text extractor fills; never inferred from party size.
    mobility: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.adults or self.children or self.children_ages or self.mobility)


class TripRequirements(BaseModel):
    """Everything the route must respect, in one place.

    Built once per request (and rebuilt from the *delta* on a refinement turn,
    while the surviving hard requirements carry over from the base plan).
    """

    locale: Literal["ru", "en"] = "ru"
    raw_query: str = ""

    party: PartyComposition = Field(default_factory=PartyComposition)

    # None = the user named no limit: the route is not trimmed to fit one.
    budget_minutes: int | None = None

    # Canonical costing for the Valhalla matrix ("pedestrian", "bicycle", ...).
    costing: str | None = None

    # Human-readable origin coordinates, when the device supplied them.
    origin_lat: float | None = None
    origin_lon: float | None = None

    # Resolved area slugs the request is restricted to (e.g. "grodno-old-town").
    areas: list[str] = Field(default_factory=list)

    result_mode: ResultMode = "route"
    round_trip: bool = False

    requirements: list[Requirement] = Field(default_factory=list)

    # Things the user asked for that the system cannot represent or prove
    # ("без лестниц" without a step-free graph). Surfaced to the user instead
    # of being silently dropped.
    unknowns: list[str] = Field(default_factory=list)

    # How the requirements were obtained. "mixed" = LLM plus explicit UI fields.
    source: Literal["llm", "explicit", "fallback", "mixed"] = "fallback"

    # ── Queries the planner/verifier actually use ───────────────────────────

    def hard(self) -> list[Requirement]:
        return [r for r in self.requirements if r.strength == "hard"]

    def soft(self) -> list[Requirement]:
        return [r for r in self.requirements if r.strength == "soft"]

    def of_kind(self, kind: RequirementKind) -> list[Requirement]:
        return [r for r in self.requirements if r.kind == kind]

    def hard_service_codes(self) -> list[str]:
        """Category codes that MUST appear on the route."""
        return [
            r.code for r in self.hard() if r.kind == "service" and r.code
        ]

    def soft_service_codes(self) -> list[str]:
        return [
            r.code for r in self.soft() if r.kind == "service" and r.code
        ]

    def must_visit_ids(self) -> list[int]:
        return [
            r.place_id for r in self.of_kind("must_visit") if r.place_id is not None
        ]

    def must_visit_names(self) -> list[str]:
        return [r.name for r in self.of_kind("must_visit") if r.name]

    def avoid_codes(self) -> list[str]:
        return [r.code for r in self.of_kind("avoid") if r.code]

    def interest_codes(self) -> list[str]:
        return [r.code for r in self.of_kind("interest") if r.code]

    def unresolved_hard(self) -> list[Requirement]:
        """Hard requirements still pending — the route cannot be reported ready."""
        return [r for r in self.hard() if r.status == "pending"]

    def failed_hard(self) -> list[Requirement]:
        """Hard requirements the verifier could not satisfy or prove."""
        return [r for r in self.hard() if r.status in ("unmet", "uncertain")]

    def is_ready(self) -> bool:
        """True only when every hard requirement is proven satisfied."""
        return not self.failed_hard() and not self.unresolved_hard()

    # ── Public projection ───────────────────────────────────────────────────

    def public_requirements(self) -> list[dict]:
        """Localizable view of the requirement list for the HTTP response.

        The frontend renders these from `kind`/`code`/`status` so both locales
        share one contract; `label`/`reason` stay diagnostic only.
        """
        return [
            {
                "kind": r.kind,
                "strength": r.strength,
                "code": r.code,
                "status": r.status,
                "place_ids": list(r.place_ids),
                "detail": r.reason,
            }
            for r in self.requirements
        ]
