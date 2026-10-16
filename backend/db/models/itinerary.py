"""The `itineraries` rows: a curated walk and the stops it names."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Itinerary:
    """One curated walk, in the order the catalogue shows it."""

    id: str
    title: str
    blurb: str = ""
    transport: str = "pedestrian"
    position: int = 0
    stops: tuple[str, ...] = ()
