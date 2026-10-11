"""Places as the measurement's currency: a `places` row → a `Candidate`.

A leaf concern — it reads the row shape and writes the measurement shape, and
neither a repository nor a connection is involved.
"""

from __future__ import annotations

from db.models.place import Place
from db.store.mappers import parse_fun_facts, parse_links, photo_of
from routing.models import Candidate, Photo


def _photo_model(place: Place) -> Photo | None:
    """The point's picture as a model, or nothing at all.

    ``photo_of`` answers a plain dict (or None); the currency carries a ``Photo``.
    """
    raw = photo_of(place)
    return Photo(**raw) if raw else None


def candidate_of(place: Place, rrf_score: float) -> Candidate:
    """A retrieved place as the measurement's working currency."""
    # A fetched row always has one; the model allows None only so a row can be
    # built before it is inserted.
    return Candidate(
        id=place.id or 0,
        name=place.name,
        category=place.category,
        lat=place.lat,
        lon=place.lon,
        blurb=place.blurb,
        fun_fact=place.fun_fact,
        fun_facts=parse_fun_facts(place.fun_facts),
        links=parse_links(place.links),
        opening_hours=place.opening_hours,
        ticket_price=place.ticket_price,
        town=place.town,
        district=place.district,
        photo=_photo_model(place),
        visit_minutes_db=place.visit_minutes,
        relevance=rrf_score,
        rrf_score=rrf_score,
    )
