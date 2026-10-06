"""The full point catalogue: `GET /places` and the payload it serves.

Two layers on purpose:

  * offline — a fake connection pins the contract: a place is described by the
    same payload a ready-made route uses, the browse order is stable, and the
    count is honest;
  * live — over HTTP against the running backend, skipped when :8080 is not
    answering, because the property that matters is that the endpoint really
    returns the dataset with coordinates on every row.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.places import list_places, place_payload

BASE_URL = os.environ.get("SMOKE_BASE_URL", "http://localhost:8080")


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
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def cursor(self, row_factory=None) -> _FakeCursor:
        return _FakeCursor(self.rows)


def _row(**over: object) -> dict:
    base = {
        "id": 1,
        "source_url": "osm:one",
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
        "fun_facts": None,
        "links": None,
        "ticket_price": None,
        "photo_url": None,
        "photo_author": None,
        "photo_license": None,
        "photo_source": None,
    }
    base.update(over)
    return base


def test_place_payload_carries_the_facts_a_card_prints():
    payload = place_payload(
        _row(
            id=10,
            source_url="osm:old-castle",
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
    )
    assert payload["place_id"] == 10
    assert payload["source_url"] == "osm:old-castle"
    assert payload["name"] == "Старый замок (Гродно)"
    assert payload["lat"] == 53.6791 and payload["lon"] == 23.8216
    assert payload["visit_minutes"] == 90
    assert payload["opening_hours"] == "вт–вс 10:00–18:00"
    # The JSON-holding columns are parsed, not passed through raw.
    assert payload["fun_facts"] == []
    assert payload["links"] == []


def test_list_places_returns_every_row_with_a_stable_shape():
    conn = _FakeConn(
        [
            _row(id=1, category="замок", name="А", town="Гродно"),
            _row(id=2, category="костёл", name="Б", town="Лида"),
        ]
    )
    answer = list_places(conn)

    assert answer["total"] == 2
    assert answer["capped"] is False
    # The browse order is the SQL's own; the payload only re-describes each row.
    assert [item["place_id"] for item in answer["items"]] == [1, 2]
    for item in answer["items"]:
        assert item["name"] and item["lat"] and item["lon"]


def _live_available() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE_URL}/health", timeout=3) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


@pytest.mark.skipif(
    not _live_available(), reason=f"backend not answering at {BASE_URL}"
)
def test_live_places_returns_the_dataset_with_coordinates():
    with urllib.request.urlopen(f"{BASE_URL}/places", timeout=10) as r:
        assert r.status == 200
        payload = json.loads(r.read())

    assert payload["items"], "the catalogue must not be empty"
    assert payload["total"] == len(payload["items"])
    for item in payload["items"]:
        assert item["name"], "every point has a name"
        assert item["lat"] and item["lon"], "every point has coordinates"
