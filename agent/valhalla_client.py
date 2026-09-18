"""Thin Valhalla HTTP client. Returns (shape, summary) for a list of locations."""

from __future__ import annotations

import json
from typing import Iterable

import httpx


def route_through(base_url: str, locations: Iterable[dict], costing: str = "pedestrian"):
    """Call GET /route. Locations are [{lat, lon, type}], first/last should be 'break'.

    Returns:
        shape   — GeoJSON LineString geometry for the trip (or None on failure).
        summary — Valhalla trip.summary dict (km, seconds) or None.
    """
    payload = {
        "costing": costing,
        "locations": list(locations),
        "units": "kilometers",
        "language": "ru",
        "alternates": 0,
        "directions_options": {"units": "kilometers"},
    }
    r = httpx.get(
        f"{base_url.rstrip('/')}/route",
        params={"json": json.dumps(payload, separators=(",", ":"))},
        timeout=30.0,
    )
    r.raise_for_status()
    body = r.json()
    if "trip" not in body:
        raise RuntimeError(f"valhalla /route returned no trip: {body}")
    trip = body["trip"]
    legs = trip.get("legs", [])
    coords = []
    for leg in legs:
        for shape_leg in leg.get("shape", "").split():
            lon_str, lat_str = shape_leg.split(",")
            coords.append([float(lon_str), float(lat_str)])
    shape = {"type": "LineString", "coordinates": coords}
    return shape, trip.get("summary")
