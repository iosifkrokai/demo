"""Ready-made routes: the curated file, its resolution against the dataset, and
the live endpoint that serves it.

Two layers on purpose:

  * offline — a fake connection pins the contract: authored order, totals read
    from the data, a stop key that no longer resolves is *reported* rather than
    silently dropped, and a stop shared by two itineraries is fetched once;
  * live — over HTTP against the running backend, skipped when :8080 is not
    answering, because the property that actually matters is that every key in
    the shipped file resolves against the real database.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import constants
from agent.itineraries import (
    ItinerariesUnavailable,
    load_itineraries,
    resolve_itineraries,
)

BASE_URL = os.environ.get("SMOKE_BASE_URL", "http://localhost:8080")

VALID_KEY_PREFIXES = ("city:", "region:", "osm:", "osm_poi:")


# ── The curated file ────────────────────────────────────────────────────────


def test_file_carries_what_a_card_needs():
    for item in load_itineraries():
        assert item["id"].strip()
        assert item["title"].strip()
        assert item["blurb"].strip()
        assert item["stops"], f"{item['id']}: no stops"
        assert item["transport"] in ("pedestrian", "bicycle", "auto")


def test_ids_are_unique_and_stops_use_provenance_keys():
    items = load_itineraries()
    ids = [item["id"] for item in items]
    assert len(set(ids)) == len(ids)
    for item in items:
        for key in item["stops"]:
            # A database id would not survive a re-seed; a source_url does.
            assert key.startswith(VALID_KEY_PREFIXES), f"{item['id']}: {key}"
            assert not key.split(":", 1)[1].isdigit(), f"{item['id']}: {key}"


def test_a_broken_file_raises_instead_of_looking_empty(tmp_path):
    broken = tmp_path / "itineraries.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ItinerariesUnavailable):
        load_itineraries(broken)

    empty = tmp_path / "empty.json"
    empty.write_text('{"itineraries": []}', encoding="utf-8")
    with pytest.raises(ItinerariesUnavailable):
        load_itineraries(empty)


# ── Resolution against the dataset ──────────────────────────────────────────


class _FakeCursor:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.executed: list[tuple[str, object]] = []

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: object = None) -> None:
        self.executed.append((sql, params))

    def fetchall(self) -> list[dict]:
        return self._rows


class _FakeConn:
    """Only what `_resolve_stops` uses: a cursor with a dict row factory."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.queries = 0

    def cursor(self, row_factory=None) -> _FakeCursor:
        self.queries += 1
        return _FakeCursor(self.rows)


def _row(source_url: str, **over: object) -> dict:
    base = {
        "id": 1,
        "source_url": source_url,
        "name": "Точка",
        "category": "замок",
        "town": "Гродно",
        "district": None,
        "lat": 53.68,
        "lon": 23.82,
        "visit_minutes": 30,
        "opening_hours": None,
        "blurb": None,
        "fun_fact": None,
    }
    base.update(over)
    return base


ITEMS = [
    {
        "id": "a",
        "title": "Маршрут А",
        "blurb": "первый",
        "transport": "pedestrian",
        "stops": ["city:one", "city:shared"],
    },
    {
        "id": "b",
        "title": "Маршрут Б",
        "blurb": "второй",
        "transport": "auto",
        "stops": ["city:shared", "city:gone"],
    },
]


def test_keeps_the_authored_order_and_totals_the_stops():
    conn = _FakeConn(
        [
            _row("city:one", id=10, name="Первая", visit_minutes=45),
            _row("city:shared", id=11, name="Общая", visit_minutes=15),
        ]
    )
    items, _missing = resolve_itineraries(conn, ITEMS)

    assert [i["id"] for i in items] == ["a", "b"]  # authored order survives
    assert [s["name"] for s in items[0]["stops"]] == ["Первая", "Общая"]
    assert items[0]["stop_count"] == 2
    assert items[0]["visit_minutes"] == 60
    # The shared stop is fetched once for the whole file, not once per route.
    assert conn.queries == 1


def test_a_stop_that_no_longer_resolves_is_reported_not_hidden():
    conn = _FakeConn([_row("city:shared", id=11, name="Общая", visit_minutes=15)])
    items, missing = resolve_itineraries(conn, ITEMS)

    assert missing == ["city:one", "city:gone"]
    # Route «b» loses its unknown stop and says so by its own count.
    assert items[1]["stop_count"] == 1
    assert [s["source_url"] for s in items[1]["stops"]] == ["city:shared"]


def test_stop_payload_carries_the_facts_a_card_prints():
    conn = _FakeConn(
        [
            _row(
                "city:one",
                id=10,
                name="Старый замок (Гродно)",
                category="замок",
                town="Гродно",
                lat=53.6791,
                lon=23.8216,
                visit_minutes=90,
                opening_hours="вт–вс 10:00–18:00",
                blurb="Блиц",
                fun_fact="Факт",
            )
        ]
    )
    items, _ = resolve_itineraries(conn, [ITEMS[0]])
    stop = items[0]["stops"][0]
    assert stop["place_id"] == 10
    assert stop["name"] == "Старый замок (Гродно)"
    assert stop["lat"] == 53.6791 and stop["lon"] == 23.8216
    assert stop["visit_minutes"] == 90
    assert stop["opening_hours"] == "вт–вс 10:00–18:00"


def test_a_service_in_the_authored_file_is_not_a_stop():
    # Regression: «С детьми: замки и парк» lists a toilet among its stops, and
    # the resolver used to hand it back as a numbered stop — a toilet where the
    # guide promised a sight. The taxonomy decides, so it comes back as a
    # service beside the route: not numbered, not counted, not timed.
    conn = _FakeConn(
        [
            _row("city:one", category="замок", visit_minutes=90),
            _row("city:shared", name="Туалет", category="туалет", visit_minutes=10),
        ]
    )
    items, _ = resolve_itineraries(conn, [ITEMS[0]])

    stops = items[0]["stops"]
    assert [s["category"] for s in stops] == ["замок"]
    assert items[0]["stop_count"] == 1
    # The toilet keeps its curated 10 minutes out of the route's visit time.
    assert items[0]["visit_minutes"] == 90

    services = items[0]["services"]
    assert [s["name"] for s in services] == ["Туалет"]
    assert services[0]["source_url"] == "city:shared"


def test_an_unmapped_category_counts_as_a_stop_not_as_a_service():
    # The taxonomy only ever gains codes; a code we do not know is more honest
    # as a destination than as a café nobody can find.
    conn = _FakeConn([_row("city:one", category="вертолётная площадка")])
    items, _ = resolve_itineraries(conn, [ITEMS[0]])
    assert items[0]["stop_count"] == 1
    assert items[0]["services"] == []


def test_visit_minutes_of_unknown_length_stays_zero_not_none():
    conn = _FakeConn([_row("city:one", visit_minutes=None)])
    items, _ = resolve_itineraries(conn, [ITEMS[0]])
    # Summing None would raise; a stop whose curated time is unknown simply
    # adds nothing to the card's «осмотр» figure.
    assert items[0]["visit_minutes"] == 0
    assert items[0]["stops"][0]["visit_minutes"] is None


# ── Live: every shipped key resolves against the real dataset ───────────────


def _live_available() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE_URL}/health", timeout=3) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


# Only this check needs the running backend; the checks above are offline and
# must keep running in a plain `pytest tests -q`.
@pytest.mark.skipif(
    not _live_available(), reason=f"backend not answering at {BASE_URL}"
)
def test_live_every_stop_key_in_the_shipped_file_resolves():
    with urllib.request.urlopen(f"{BASE_URL}/routes/itineraries", timeout=10) as r:
        assert r.status == 200
        payload = json.loads(r.read())

    assert payload["missing"] == [], f"unresolved stop keys: {payload['missing']}"
    authored = {item["id"]: item for item in load_itineraries()}
    assert {item["id"] for item in payload["items"]} == set(authored)

    for item in payload["items"]:
        want = len(authored[item["id"]]["stops"])
        # Every authored key comes back — as a stop or as a service beside the
        # route. Nothing may go missing just because it changed role.
        assert item["stop_count"] + len(item.get("services", [])) == want, (
            f"{item['id']}: stops went missing"
        )
        assert item["visit_minutes"] > 0
        for stop in item["stops"]:
            assert stop["name"] and stop["lat"] and stop["lon"]


@pytest.mark.skipif(
    not _live_available(), reason=f"backend not answering at {BASE_URL}"
)
def test_live_no_curated_stop_is_a_service():
    """The dataset invariant behind the user's complaint: a toilet was a stop.

    Checked against the API rather than the file, because the role comes from
    the taxonomy in the database, not from the authored keys.
    """
    with urllib.request.urlopen(f"{BASE_URL}/routes/itineraries", timeout=10) as r:
        payload = json.loads(r.read())

    offenders = [
        (item["id"], stop["name"])
        for item in payload["items"]
        for stop in item["stops"]
        if stop["category"] in constants.CONVENIENCE_CATEGORIES
    ]
    assert offenders == [], f"услуги стоят остановками: {offenders}"

    # The toilet the author put on the way of «С детьми: замки и парк» is still
    # in the answer — as a service of that route, not thrown away.
    with_kids = payload["items"]
    services = [
        service
        for item in with_kids
        for service in item.get("services", [])
        if service["category"] == "туалет"
    ]
    assert services, "авторский туалет должен оставаться в маршруте как услуга"
