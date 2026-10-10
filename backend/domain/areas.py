"""Named areas and the single project-area geofence predicate.

Area definitions live in data/areas.json; geometry is loaded at runtime.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
AREAS_PATH = DATA_DIR / "areas.json"
GRODNO_BORDER_PATH = DATA_DIR / "grodno_border.json"
KEEP_PATH = DATA_DIR / "belarus_border_keep.json"

LOCALES = ("ru", "en")
PROJECT_AREA_SLUG = "grodno-oblast"

_LAT_M_PER_DEG = 111_320
_LON_M_PER_DEG = 111_320 * 0.6
_DEFAULT_EXCEPTION_RADIUS_M = 300.0


@lru_cache(maxsize=1)
def _areas_document() -> dict[str, Any]:
    return json.loads(AREAS_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_areas() -> dict[str, dict[str, Any]]:
    """slug -> area definition; fails fast on duplicate slugs or aliases."""
    areas: dict[str, dict[str, Any]] = {}
    claimed_by: dict[str, str] = {}
    for area in _areas_document()["areas"]:
        slug = area["slug"]
        if slug in areas:
            raise ValueError(f"areas.json: duplicate slug {slug!r}")
        terms = [slug, area.get("name_ru", ""), area.get("name_en", "")]
        for locale in LOCALES:
            terms.extend(area.get("aliases", {}).get(locale, []))
        for term in terms:
            key = _normalize(term)
            if not key:
                continue
            if key in claimed_by and claimed_by[key] != slug:
                raise ValueError(
                    f"areas.json: {key!r} claimed by both {claimed_by[key]!r} "
                    f"and {slug!r}"
                )
            claimed_by[key] = slug
        areas[slug] = area
    return areas


def area_lookup_by_slug(slug: str | None) -> dict[str, Any] | None:
    """Area definition for ``slug`` with geometry provenance and exceptions.

    Returns ``None`` for an unknown slug.
    """
    if not slug:
        return None
    area = load_areas().get(slug)
    if area is None:
        return None
    resolved = dict(area)
    geometry = area.get("geometry")
    if isinstance(geometry, dict) and geometry.get("source") == GRODNO_BORDER_PATH.name:
        resolved["geometry"] = {
            **geometry,
            "loaded_from": GRODNO_BORDER_PATH.name,
            "rings": _grodno_rings(),
        }
        resolved["exceptions"] = [dict(exc) for exc in _exceptions()]
    return resolved


def resolve_area(term: str | None, locale: str | None = None) -> str | None:
    """Resolve a user-facing term to an area slug, or ``None``.

    Matching is exact after normalization against slug, RU/EN names and aliases.
    """
    if not isinstance(term, str):
        return None
    needle = _normalize(term)
    if not needle:
        return None
    order = [locale] if locale in LOCALES else []
    order += [loc for loc in LOCALES if loc not in order]
    for loc in order:
        for slug, area in load_areas().items():
            if needle == _normalize(slug) or needle == _normalize(
                area.get(f"name_{loc}", "")
            ):
                return slug
            for alias in area.get("aliases", {}).get(loc, []):
                if needle == _normalize(alias):
                    return slug
    return None


def area_contains(slug: str | None, lat: float | None, lon: float | None) -> bool | None:
    """Containment of one point in one named area.

    ``True``/``False`` when the area has geometry; ``None`` when unknown or no polygon.
    """
    if slug is None or lat is None or lon is None:
        return None
    area = load_areas().get(slug)
    if area is None:
        return None
    geometry = area.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("source") != GRODNO_BORDER_PATH.name:
        return None
    return any(point_in_ring(lat, lon, ring) for ring in _grodno_rings())


def in_project_area(lat: float | None, lon: float | None) -> bool:
    """The single project-area predicate: Grodno Oblast + documented exceptions.

    Shared by import-time validation and query-time filtering; False for unknown points.
    """
    if lat is None or lon is None:
        return False
    if any(point_in_ring(lat, lon, ring) for ring in _grodno_rings()):
        return True
    return any(
        _within_radius(lat, lon, exc["lat"], exc["lon"], exc["radius_m"])
        for exc in _exceptions()
    )


@lru_cache(maxsize=1)
def _grodno_rings() -> tuple[tuple[tuple[float, float], ...], ...]:
    """Rings as (lon, lat) tuples, loaded from the geoBoundaries ADM1 file."""
    data = json.loads(GRODNO_BORDER_PATH.read_text(encoding="utf-8"))
    return tuple(tuple((float(lon), float(lat)) for lon, lat in ring) for ring in data["rings"])


@lru_cache(maxsize=1)
def _exceptions() -> tuple[dict[str, Any], ...]:
    """Documented boundary exceptions (belarus_border_keep.json) as data."""
    data = json.loads(KEEP_PATH.read_text(encoding="utf-8"))
    default_radius = float(data.get("radius_m", _DEFAULT_EXCEPTION_RADIUS_M))
    exceptions = []
    for point in data["points"]:
        exceptions.append(
            {
                "name": point.get("name", ""),
                "lat": float(point["lat"]),
                "lon": float(point["lon"]),
                "radius_m": float(point.get("radius_m", default_radius)),
                "source": KEEP_PATH.name,
            }
        )
    return tuple(exceptions)


def point_in_ring(lat: float, lon: float, ring: tuple[tuple[float, float], ...]) -> bool:
    """Ray casting: count crossings of the horizontal ray going east from the point."""
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > lat) != (y2 > lat):
            x_cross = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x_cross:
                inside = not inside
    return inside


def _within_radius(
    lat: float, lon: float, center_lat: float, center_lon: float, radius_m: float
) -> bool:
    if abs(lat - center_lat) * _LAT_M_PER_DEG > radius_m:
        return False
    if abs(lon - center_lon) * _LON_M_PER_DEG > radius_m:
        return False
    d_lat = (lat - center_lat) * _LAT_M_PER_DEG
    d_lon = (lon - center_lon) * _LON_M_PER_DEG
    return (d_lat * d_lat + d_lon * d_lon) ** 0.5 <= radius_m


def _normalize(term: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace — for exact matching."""
    squashed = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", term.lower()))
    return squashed.strip()
