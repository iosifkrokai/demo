"""The bbox → Overpass query mapping, pinned.

seed.overpass documents its bbox as (W, S, E, N) — DEFAULT_BBOX, the ``fetch``
``--bbox`` metavar, the module usage example and agent/constants.GRODNO_BBOX all
say so — while Overpass QL needs (south, west, north, east) inside the ``(...)``
filter ("the values are, in order: southern-most latitude, western-most
longitude, northern-most latitude, eastern-most longitude"). The old
fetch_overpass used to unpack the tuple as (S, W, N, E) instead: nothing raised,
the voblast box became a valid-looking box over the Indian Ocean, so a live run
silently produced an empty CSV. The public order therefore stays (W, S, E, N)
and build_overpass_query() does the remap.

No network, no DB: the query string is built from a plain tuple, and the
fetch_overpass test stubs httpx.post to capture what would have been sent.
"""

from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from domain.constants import GRODNO_BBOX
from seed.cli import build_arg_parser
from seed.overpass import (
    DEFAULT_BBOX,
    SIGHT_QUERY,
    build_overpass_query,
    fetch_overpass,
)

# Every coordinate is distinct so a swap of any two of them is visible.
WEST, SOUTH, EAST, NORTH = 23.0, 52.0, 28.0, 55.0
BBOX = (WEST, SOUTH, EAST, NORTH)  # (W, S, E, N) — this module's public order
# What the same four numbers must become inside the query: (S, W, N, E).
OVERPASS_COORDS = (SOUTH, WEST, NORTH, EAST)

# The 4 coordinate slots of one `nwr[...](...)` filter.
_FILTER_RE = re.compile(r"nwr\[[^\]]*\]\(([^)]*)\);")


def _filter_coords(query: str) -> list[tuple[str, ...]]:
    """Coordinates of every bbox filter in the query, in order."""
    return [tuple(m.group(1).split(",")) for m in _FILTER_RE.finditer(query)]


def _fetch_parser():
    """The ``fetch`` subparser, where ``--bbox`` now lives."""
    parser = build_arg_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    return sub.choices["fetch"]


# the mapping itself

def test_public_order_is_wsen():
    """The tuple this module accepts is (W, S, E, N) — pinned, not implied."""
    assert DEFAULT_BBOX == (23.35, 52.75, 27.00, 54.80)
    # The documented values are only sane as (W, S, E, N): 23.35/27.00 are
    # longitudes, 52.75/54.80 are latitudes.
    west, south, east, north = DEFAULT_BBOX
    assert west < east
    assert south < north
    assert 20.0 < west < 30.0  # Belarus is lon 23..28
    assert 50.0 < south < 56.0  # ... and lat 52..55


def test_default_bbox_is_the_voblast_box_from_agent_constants():
    """DEFAULT_BBOX is the same box as GRODNO_BBOX, in this module's order."""
    assert (
        GRODNO_BBOX["west"],
        GRODNO_BBOX["south"],
        GRODNO_BBOX["east"],
        GRODNO_BBOX["north"],
    ) == DEFAULT_BBOX


def test_bbox_maps_to_overpass_swne_order():
    query = build_overpass_query(SIGHT_QUERY, BBOX)
    coords = _filter_coords(query)
    assert coords, "SIGHT_QUERY has no nwr bbox filter to check"
    for c in coords:
        assert c == tuple(str(v) for v in OVERPASS_COORDS)
    # No unformatted placeholder survived (a typo'd {north} would raise instead,
    # but a missing filter would leave the list short — hence the loop above).
    assert "{" not in query and "}" not in query


def test_voblast_default_box_builds_the_voblast_query():
    """The regression itself: the old code produced (23.35,52.75,27.0,54.8)."""
    query = build_overpass_query(SIGHT_QUERY, DEFAULT_BBOX)
    for c in _filter_coords(query):
        assert c == ("52.75", "23.35", "54.8", "27.0")  # (S, W, N, E)


def test_every_filter_in_the_template_uses_the_same_slot_order():
    """A filter left in a different order would silently drop half the box."""
    template_coords = _filter_coords(SIGHT_QUERY)
    assert len(template_coords) >= 4
    assert all(c == tuple(f"{{{n}}}" for n in ("south", "west", "north", "east"))
               for c in template_coords)


def test_build_query_rejects_a_swapped_or_inverted_box():
    """A box that cannot be (W, S, E, N) fails loudly instead of querying elsewhere."""
    with pytest.raises(ValueError, match=r"order is \(W, S, E, N\)"):
        build_overpass_query(SIGHT_QUERY, (EAST, SOUTH, WEST, NORTH))  # W > E
    with pytest.raises(ValueError, match=r"order is \(W, S, E, N\)"):
        build_overpass_query(SIGHT_QUERY, (WEST, NORTH, EAST, SOUTH))  # S > N


# fetch_overpass actually sends the mapped query

def test_fetch_overpass_posts_the_mapped_query(monkeypatch):
    """No network: httpx.post is stubbed, the outgoing query is captured."""
    sent: list[str] = []

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"elements": []}

    def fake_post(_url, data=None, **_kwargs):
        sent.append(data["data"])
        return _Resp()

    monkeypatch.setattr("seed.overpass.httpx.post", fake_post)

    assert fetch_overpass(build_overpass_query(SIGHT_QUERY, BBOX)) == []
    assert len(sent) == 1
    for c in _filter_coords(sent[0]):
        assert c == tuple(str(v) for v in OVERPASS_COORDS)


# the CLI agrees with the mapping

def test_cli_bbox_flag_is_documented_as_wsen():
    # The flag kept its metavar and arity; the help text moved with the flag to
    # `python -m seed fetch --bbox "W S E N"`.
    action = next(a for a in _fetch_parser()._actions if a.dest == "bbox")
    assert action.metavar == ("W", "S", "E", "N")
    assert action.nargs == 4
    help_text = " ".join(action.help.split())
    assert "W S E N" in help_text


def test_cli_bbox_values_flow_into_the_query_unchanged():
    """`--bbox 23.0 52.0 28.0 55.0` (the doc example) builds the SWNE query."""
    args = build_arg_parser().parse_args(
        ["fetch", "--bbox", "23.0", "52.0", "28.0", "55.0"])
    assert tuple(args.bbox) == BBOX
    for c in _filter_coords(build_overpass_query(SIGHT_QUERY, tuple(args.bbox))):
        assert c == tuple(str(v) for v in OVERPASS_COORDS)
