"""Thin Valhalla HTTP client. Returns (shape, summary) for a list of locations."""

from __future__ import annotations

import json
from typing import Iterable

import httpx

# Valhalla encodes leg shapes as an encoded polyline (same alphabet and delta
# scheme as Google's) with 1e6 precision, not as "lon,lat lon,lat" pairs.
_PRECISION = 1e6


def _decode_polyline(encoded: str) -> list[list[float]]:
    coords: list[list[float]] = []
    lat = lon = 0
    i = 0
    while i < len(encoded):
        for axis in range(2):  # 0 = latitude, 1 = longitude
            shift = result = 0
            while True:
                byte = ord(encoded[i]) - 63
                i += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lon += delta
        coords.append([lon / _PRECISION, lat / _PRECISION])
    return coords


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
    coords: list[list[float]] = []
    for leg in legs:
        coords.extend(_decode_polyline(leg.get("shape", "")))
    shape = {"type": "LineString", "coordinates": coords}
    return shape, trip.get("summary")
