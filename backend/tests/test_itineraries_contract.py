"""Every stop of a ready-made route must be a place the seed actually projects.

A stop is keyed on `places.source_url`, and nothing in the seed path checks that the
key exists: `--dry-run` never reads the itinerary file, and an unknown key is silently
dropped by `db/store/itineraries.py`, showing up only as the «часть точек не нашлась»
banner on a live stack. This is the contract that keeps a ready-made route from rotting
when a dataset is re-fetched under it.
"""

from __future__ import annotations

from typing import Any

from db.seed import datasets as ds_mod, itineraries as itin_mod, taxonomy as tax_mod
from db.seed.cli import _collect
from routing.valhalla.constants import VALHALLA_COSTINGS


def _projected() -> dict[str, dict[str, Any]]:
    """The records the seed would write, keyed by the natural key a stop uses."""
    collected, _shadowed, _stale = _collect(ds_mod.default_datasets())
    return {record["source_url"]: record for record in collected["records"]}


def _walks() -> list[dict[str, Any]]:
    return itin_mod.load_itineraries()


def _roles() -> dict[str, str]:
    return {category.code: category.role for category in tax_mod.all_categories()}


def test_every_stop_resolves_to_a_place() -> None:
    projected = _projected()
    unresolved = {
        walk["id"]: [key for key in walk["stops"] if key not in projected] for walk in _walks()
    }
    assert not {walk: keys for walk, keys in unresolved.items() if keys}, (
        f"a stop names a source_url no dataset holds: {unresolved}"
    )


def test_ids_are_unique_and_stops_repeat_nowhere() -> None:
    walks = _walks()
    ids = [walk["id"] for walk in walks]
    assert len(ids) == len(set(ids)), f"duplicate itinerary id: {ids}"

    repeated = {walk["id"]: keys for walk in walks if len(keys := walk["stops"]) != len(set(keys))}
    assert not repeated, f"a walk stops at the same place twice: {repeated}"


def test_a_walk_is_at_least_two_sights_worth_taking() -> None:
    roles = _roles()
    projected = _projected()
    thin = {}
    for walk in _walks():
        sights = [
            key
            for key in walk["stops"]
            if roles.get(projected.get(key, {}).get("category", "")) != "service"
        ]
        if len(sights) < 2:
            thin[walk["id"]] = walk["stops"]
    assert not thin, f"a walk has fewer than two sight stops: {thin}"


def test_transport_is_a_costing_the_engine_knows() -> None:
    unknown = {
        walk["id"]: walk["transport"]
        for walk in _walks()
        if walk["transport"] not in VALHALLA_COSTINGS
    }
    assert not unknown, f"a walk asks the engine for a transport it cannot route: {unknown}"


def test_every_walk_says_what_it_is() -> None:
    blank = [
        walk["id"]
        for walk in _walks()
        if not str(walk.get("title") or "").strip() or not str(walk.get("blurb") or "").strip()
    ]
    assert not blank, f"a walk carries no title or no blurb: {blank}"
