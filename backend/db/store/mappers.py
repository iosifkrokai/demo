"""Rows to models, and models to the payloads the panel prints.

The parsers live here rather than in a model because they decode a *stored*
column, not a shape: `fun_facts` and `links` are JSON in the region dataset and
pipe-delimited text in the curated one, and the table still holds both.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from typing import Any, TypedDict

from db.models.area import Area
from db.models.place import Place


def model_from_row[Model](model: type[Model], row: Mapping[str, Any]) -> Model:
    """A database row as a model of its table.

    Columns the query did not ask for are simply absent, and a computed column
    (`cosine_dist`, `_name_sim`) is ignored — so every SELECT can share one
    column list without the mapper caring.
    """
    known = {field.name for field in dataclasses.fields(model)}  # type: ignore[arg-type]
    return model(**{key: value for key, value in row.items() if key in known})  # type: ignore[call-arg]


def place_from_row(row: Mapping[str, Any]) -> Place:
    """One `places` row."""
    return model_from_row(Place, row)


def area_from_row(row: Mapping[str, Any]) -> Area:
    """One `areas` row."""
    return model_from_row(Area, row)


def parse_fun_facts(raw: str | None) -> list[str]:
    """Accept both '["a","b"]' JSON (region dataset) and 'a|b' pipe format (curated)."""
    raw = (raw or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            return [str(x).strip() for x in json.loads(raw) if str(x).strip()][:3]
        except json.JSONDecodeError:
            pass
    return [f.strip() for f in raw.split("|") if f.strip()][:3]


def parse_links(raw: str | None) -> list[dict]:
    """Accept both '[{"title":...}]' JSON and 'title | url' pipe items."""
    raw = (raw or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            return [x for x in json.loads(raw) if isinstance(x, dict)][:4]
        except json.JSONDecodeError:
            pass
    links: list[dict] = []
    for item in raw.split("|"):
        item = item.strip()
        if not item:
            continue
        if " | " in item:
            title, url = (s.strip() for s in item.split(" | ", 1))
            links.append({"title": title, "url": url})
        else:
            try:
                links.append(json.loads(item))
            except Exception:
                pass
    return links[:4]


def photo_of(place: Place) -> dict | None:
    """The point's picture with its credit — or nothing at all.

    A URL without its author and licence is not shown: Wikimedia files are
    licensed, so "photo_url set, author missing" is a violation waiting to happen.
    """
    url = (place.photo_url or "").strip()
    if not url:
        return None
    author = (place.photo_author or "").strip()
    license_name = (place.photo_license or "").strip()
    if not author or not license_name:
        return None
    return {
        "url": url,
        "author": author,
        "license": license_name,
        "source": (place.photo_source or "").strip() or None,
    }


def place_payload(place: Place) -> dict[str, Any]:
    """One place, in the shape the panel prints. Numbers stay numbers.

    The keys match `ItineraryStop` on the client.
    """
    return {
        "place_id": place.id,
        "source_url": place.source_url,
        "name": place.name,
        "category": place.category,
        "town": place.town,
        "district": place.district,
        "lat": place.lat,
        "lon": place.lon,
        "visit_minutes": place.visit_minutes,
        "opening_hours": place.opening_hours,
        "blurb": place.blurb,
        "fun_fact": place.fun_fact,
        "fun_facts": parse_fun_facts(place.fun_facts),
        "links": parse_links(place.links),
        "photo": photo_of(place),
        "ticket_price": place.ticket_price,
    }


class RouteMetrics(TypedDict):
    """The three scalars a saved-routes list row carries, `stop_count` always set."""

    stop_count: int
    distance_m: int | None
    duration_min: int | None


def route_metrics(
    stop_count: int,
    summary: dict[str, Any] | None,
    budget: dict[str, Any] | None,
) -> RouteMetrics:
    """Derive the list columns from the plan's lightweight sub-objects.

    A plan that does not carry a value yields null rather than a guessed one.
    """
    distance_m: int | None = None
    duration_min: int | None = None

    km = (summary or {}).get("length_km")
    if isinstance(km, (int, float)) and not isinstance(km, bool):
        distance_m = int(round(float(km) * 1000))

    total = (budget or {}).get("total_minutes")
    if isinstance(total, (int, float)) and not isinstance(total, bool):
        duration_min = int(round(float(total)))
    else:
        secs = (summary or {}).get("time_seconds")
        if isinstance(secs, (int, float)) and not isinstance(secs, bool):
            duration_min = int(round(float(secs) / 60.0))

    return {
        "stop_count": int(stop_count),
        "distance_m": distance_m,
        "duration_min": duration_min,
    }
