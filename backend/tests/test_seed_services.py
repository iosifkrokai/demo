"""Services ingest: tag mapping, name fallbacks, geofence, 150 m de-dup, Overpass failover.

No network and no DB — pure helpers from seed.osm_tags + seed.overpass; the
fetch_overpass tests stub httpx.post and time.sleep.
"""

from __future__ import annotations

import os
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from seed.osm_tags import (
    is_name_dup,
    service_element_to_row,
    service_name,
    service_tag_to_category,
)
from seed.overpass import (
    DEFAULT_BBOX,
    OVERPASS_ENDPOINTS,
    SERVICE_QUERY,
    build_overpass_query,
    fetch_overpass,
)


def test_tag_to_category_mapping():
    assert service_tag_to_category({"amenity": "cafe"}) == "кафе"
    assert service_tag_to_category({"amenity": "fast_food"}) == "кафе"
    assert service_tag_to_category({"amenity": "restaurant"}) == "ресторан"
    assert service_tag_to_category({"amenity": "toilets"}) == "туалет"
    assert service_tag_to_category({"tourism": "hotel"}) == "гостиница"
    assert service_tag_to_category({"tourism": "hostel"}) == "гостиница"
    assert service_tag_to_category({"tourism": "guest_house"}) == "гостиница"
    assert service_tag_to_category({"amenity": "bench"}) is None


def test_name_prefers_name_then_name_ru():
    assert service_name({"name": "Кафе А"}, "кафе") == "Кафе А"
    assert service_name({"name:ru": "Кафе Б"}, "кафе") == "Кафе Б"


def test_unnamed_toilet_gets_fallback_name():
    assert service_name({"amenity": "toilets"}, "туалет") == "Туалет"
    assert service_name({"addr:street": "ул. Советская"}, "туалет") == "Туалет (ул. Советская)"
    # Everything else without a name is dropped.
    assert service_name({"amenity": "cafe"}, "кафе") is None


def test_osm_element_to_row_rejects_foreign_point():
    el = {"type": "node", "id": 1, "lat": 54.6872, "lon": 25.2797,
          "tags": {"name": "Cafe Vilnius", "amenity": "cafe"}}
    assert service_element_to_row(el) is None


def test_osm_element_to_row_accepts_grodno_point():
    el = {"type": "node", "id": 2, "lat": 53.6772, "lon": 23.8232,
          "tags": {"name": "Кафе Ласточка", "amenity": "cafe", "addr:city": "Гродно"}}
    row = service_element_to_row(el)
    assert row is not None
    assert row["category"] == "кафе"
    assert row["source_url"] == "osm_poi:node/2"


def test_osm_element_to_row_accepts_way_with_center_in_grodno():
    """A way element with `center` coords inside Grodno must be accepted.

    The center block replaces lat/lon for non-node elements; the
    source_url must follow the ``osm_poi:way/<id>`` pattern, and
    ``tourism=hotel`` must map to the hotel category.
    """
    el = {"type": "way", "id": 12345,
          "center": {"lat": 53.6772, "lon": 23.8232},
          "tags": {"name": "Отель Гродно", "tourism": "hotel", "addr:city": "Гродно"}}
    row = service_element_to_row(el)
    assert row is not None
    assert row["category"] == "гостиница"
    assert row["source_url"] == "osm_poi:way/12345"

    # Same way, same tags — but centered in Vilnius → outside the project area.
    vilnius_el = {"type": "way", "id": 12345,
                  "center": {"lat": 54.6872, "lon": 25.2797},
                  "tags": {"name": "Отель Гродно", "tourism": "hotel", "addr:city": "Гродно"}}
    assert service_element_to_row(vilnius_el) is None


def test_is_name_dup_within_150m():
    row = {"name": "Кафе X", "lat": 53.6772, "lon": 23.8232}
    same_name_near = [{"name": "Кафе X", "lat": 53.6782, "lon": 23.8232}]  # ~111 m
    other_name_near = [{"name": "Кафе Y", "lat": 53.6782, "lon": 23.8232}]
    same_name_far = [{"name": "Кафе X", "lat": 53.7000, "lon": 23.8232}]  # ~2.5 km
    assert is_name_dup(row, same_name_near) is True
    assert is_name_dup(row, other_name_near) is False
    assert is_name_dup(row, same_name_far) is False


# ── Overpass retry/failover ──────────────────────────────────────────────────

def _http_status_error(endpoint: str, status: int) -> httpx.HTTPStatusError:
    """A real status error — what httpx's raise_for_status() raises for a 5xx."""
    request = httpx.Request("POST", endpoint)
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"Server Error {status}", request=request, response=response)


def _service_query() -> str:
    """fetch_overpass now takes an already-rendered query string."""
    return build_overpass_query(SERVICE_QUERY, DEFAULT_BBOX)


def test_fetch_overpass_504_on_one_endpoint_fails_over_to_the_next(monkeypatch):
    """A 504 from one endpoint must not stop a later endpoint's elements coming back.

    HTTPStatusError is not in fetch_overpass's dead-endpoint list, so the 504
    endpoint stays in rotation and the next endpoint in the same attempt gets
    its turn. httpx.post and time.sleep are stubbed: no network, no backoff wait.
    """
    assert len(OVERPASS_ENDPOINTS) >= 2  # the scenario needs a "later" endpoint
    failing, serving = OVERPASS_ENDPOINTS[0], OVERPASS_ENDPOINTS[1]

    valid_elements = [
        {"type": "node", "id": 42, "lat": 53.6772, "lon": 23.8232,
         "tags": {"name": "Кафе Ласточка", "amenity": "cafe"}},
    ]

    class _GatewayTimeoutResp:
        def raise_for_status(self) -> None:
            raise _http_status_error(failing, 504)

        def json(self) -> dict:
            raise AssertionError("a 504 response has no body to parse")

    class _OkResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"elements": valid_elements}

    posted: list[str] = []

    def fake_post(url, **_kwargs):
        posted.append(url)
        return _GatewayTimeoutResp() if url == failing else _OkResp()

    monkeypatch.setattr("seed.overpass.httpx.post", fake_post)
    monkeypatch.setattr("seed.overpass.time.sleep", lambda _seconds: None)

    elements = fetch_overpass(_service_query())

    assert elements == valid_elements
    # The 504 endpoint was tried first, then the very next endpoint served the
    # elements — the failover happened inside the first attempt, no sleep needed.
    assert posted == [failing, serving]


def test_fetch_overpass_all_endpoints_504_returns_none(monkeypatch):
    """When every endpoint returns 504, fetch_overpass must return None.

    HTTPStatusError is not in the dead-endpoint list, so all endpoints stay
    in rotation across all 4 configured attempts. httpx.post and time.sleep
    are stubbed: no network, no backoff wait.
    """
    posted: list[str] = []

    def fake_post(url, **_kwargs):
        posted.append(url)
        raise _http_status_error(url, 504)

    monkeypatch.setattr("seed.overpass.httpx.post", fake_post)
    monkeypatch.setattr("seed.overpass.time.sleep", lambda _seconds: None)

    result = fetch_overpass(_service_query())

    assert result is None
    # 4 attempts × every endpoint tried each attempt (504 doesn't kill rotation)
    assert len(posted) == 4 * len(OVERPASS_ENDPOINTS)
