"""Photos of points: where the URL comes from, and what is never shown.

Three layers, because "у точки есть фото" is only true if all of them hold:

  * offline rules — a Commons file title is derived from a hint, a Wikipedia
    hint turns into (wiki, article), and a record without its author or licence
    is dropped instead of rendered;
  * the shipped file — every entry in `data/place_photos.json` is a Wikimedia
    image with a credit, and its key is a real hint key, not a leftover;
  * live — against the running backend, the stops a client receives carry the
    photo; skipped when :8080 is not answering.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.error
import urllib.request
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.itineraries import _stop_payload
from agent.models import Photo, Place
from agent.planner.retrieve import parse_photo
from scripts.seed_photos import (
    commons_file_title,
    parse_wikipedia,
)

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHOTOS = os.path.join(BACKEND, "data", "place_photos.json")
HINTS = os.path.join(BACKEND, "data", "osm_photo_hints.json")
BASE_URL = os.environ.get("SMOKE_BASE_URL", "http://localhost:8080")

WIKIMEDIA_HOSTS = ("upload.wikimedia.org", "commons.wikimedia.org")

FULL_ROW = {
    "photo_url": "https://upload.wikimedia.org/wikipedia/commons/6/6a/x.jpg",
    "photo_author": "Александр Липилин",
    "photo_license": "CC BY-SA 3.0",
    "photo_source": "https://commons.wikimedia.org/wiki/File:x.jpg",
}


# ── Where a file title comes from ───────────────────────────────────────────


def test_commons_hint_becomes_a_file_title():
    assert commons_file_title("File:Гродна. Аптэка.jpg") == "File:Гродна. Аптэка.jpg"
    assert commons_file_title("Гродна. Аптэка.jpg") == "File:Гродна. Аптэка.jpg"


def test_a_category_is_not_a_photo():
    # A category lists other things; taking its first member would be a guess.
    assert commons_file_title("Category:Old castles in Grodno") is None
    assert commons_file_title("Belarus/Grodno/Farny") is None


def test_wikipedia_hint_becomes_wiki_and_article():
    assert parse_wikipedia("be:Ніжняя царква (Гродна)") == ("be", "Ніжняя царква (Гродна)")
    # A section anchor is not part of the article title.
    assert parse_wikipedia("be:Касцёл Тройцы (Ішчална)#Сонечны гадзіннік") == (
        "be",
        "Касцёл Тройцы (Ішчална)",
    )


def test_a_wikipedia_hint_that_is_not_a_wiki_is_not_used():
    assert parse_wikipedia("Ніжняя царква") is None
    assert parse_wikipedia("be:") is None
    assert parse_wikipedia("be:   ") is None


def test_the_article_comes_from_our_own_links():
    """No searching: the curated dataset already names its Wikipedia article."""
    from scripts.seed_photos import article_from_links

    raw = '[{"title":"Старый замок — Wikipedia","url":"https://ru.wikipedia.org/wiki/%D0%A1%D1%82%D0%B0%D1%80%D1%8B%D0%B9_%D0%B7%D0%B0%D0%BC%D0%BE%D0%BA_(%D0%93%D1%80%D0%BE%D0%B4%D0%BD%D0%BE)"}]'
    assert article_from_links(raw) == ("ru", "Старый замок (Гродно)")

    # A section anchor is not part of the title.
    assert article_from_links('{"url":"https://be.wikipedia.org/wiki/Царква#Гісторыя"}') == (
        "be",
        "Царква",
    )
    # Our data also has plain tourist-portal links — those give no article.
    assert article_from_links('[{"url":"https://grodno.by/"}]') is None
    assert article_from_links(None) is None


def test_distance_is_metres_not_degrees():
    from scripts.seed_photos import haversine_m

    # One thousandth of a degree of latitude is ~111 m anywhere on Earth.
    assert haversine_m(53.0, 24.0, 53.001, 24.0) == pytest.approx(111, abs=2)
    assert haversine_m(53.0, 24.0, 53.0, 24.0) == 0


def test_a_redirect_still_answers_under_the_name_we_asked(monkeypatch):
    """Half of our titles are redirects; a stub without properties is not an
    answer, and without the redirect the point silently loses its photo."""
    import scripts.seed_photos as seed

    def fake_api(endpoint, params):
        return {
            "query": {
                "redirects": [{"from": "Коложская церковь", "to": "Борисоглебская церковь"}],
                "pages": {
                    "1": {
                        "title": "Борисоглебская церковь",
                        "coordinates": [{"lat": 53.68, "lon": 23.83}],
                        "pageprops": {"wikibase_item": "Q123"},
                    }
                },
            }
        }

    monkeypatch.setattr(seed, "api", fake_api)
    meta = seed.page_meta("ru", ["Коложская церковь"])
    assert meta["Коложская церковь"]["qid"] == "Q123"
    assert meta["Коложская церковь"]["lat"] == 53.68


def test_wikidata_gives_the_image_and_the_coordinate_together(monkeypatch):
    """One call per batch, and P625 is the half that answers for more points."""
    import scripts.seed_photos as seed

    def fake_api(endpoint, params):
        return {
            "entities": {
                "Q1": {
                    "claims": {
                        "P18": [{"mainsnak": {"datavalue": {"value": "File:X.jpg"}}}],
                        "P625": [
                            {"mainsnak": {"datavalue": {"value": {"latitude": 53.1, "longitude": 24.2}}}}
                        ],
                    }
                },
                "Q2": {"claims": {}},
            }
        }

    monkeypatch.setattr(seed, "api", fake_api)
    claims = seed.wikidata_claims(["Q1", "Q2"])
    assert claims["Q1"] == {"image": "File:X.jpg", "lat": 53.1, "lon": 24.2}
    assert claims["Q2"] == {"image": None, "lat": None, "lon": None}


def test_an_article_only_counts_when_its_own_coordinates_agree():
    """The whole safety of this path: a name is not evidence, a position is."""
    from scripts.seed_photos import match_article

    place = {"name": "Костёл Святого Михаила Архангела (Сморгонь)", "lat": 54.48, "lon": 26.4}

    # The article is about the church of the same name in another town, 60 km away.
    far = {"Костёл Святого Михаила": {"lat": 54.9, "lon": 26.4, "qid": "Q1"}}
    assert match_article(place, far) is None

    # The same article standing where our point is: accepted, with the gap.
    near = {"Костёл Святого Михаила": {"lat": 54.481, "lon": 26.4, "qid": "Q1", "wiki": "ru"}}
    match = match_article(place, near)
    assert match is not None
    assert match["qid"] == "Q1"
    assert match["match_m"] <= 2000
    assert match["article"] == "ru:Костёл Святого Михаила"


def test_the_town_article_is_not_the_article_about_the_place():
    """«Гродно» sits a kilometre from Sovetskaya street and would pass a
    distance check while illustrating the wrong thing."""
    from scripts.seed_photos import is_settlement_article, match_article

    place = {"name": "Улица Советская", "town": "Гродно", "lat": 53.68, "lon": 23.82}
    assert is_settlement_article(place, "Гродно") is True
    assert is_settlement_article(place, "Городница") is False
    assert is_settlement_article({"name": "x"}, "что угодно") is False
    # Even standing right next to our point, the town article is refused.
    assert match_article(place, {"Гродно": {"lat": 53.68, "lon": 23.82, "qid": "Q1"}}) is None


def test_an_article_without_coordinates_is_not_used():
    from scripts.seed_photos import match_article

    assert (
        match_article(
            {"name": "Старый замок (Гродно)", "lat": 53.68, "lon": 23.82},
            {"Старый замок": {"lat": None, "lon": None}},
        )
        is None
    )


def test_a_credit_line_is_a_name_not_a_link():
    """Real Artist fields carry HTML, link text and doubled credits."""
    from scripts.seed_photos import clean_author

    assert clean_author('<a href="x">Александр Липилин</a>') == "Александр Липилин"
    assert clean_author("Валацуга (https://fgb.by/view/1)") == "Валацуга"
    assert clean_author("Unknown authorUnknown author") == "Unknown author"
    assert clean_author("Michal Gorski") == "Michal Gorski"


def test_a_rate_limit_is_waited_out_not_fatal(monkeypatch):
    """Wikimedia really does answer 429; the reseed has to survive it."""
    import email.message
    import io
    import urllib.error

    import scripts.seed_photos as seed

    monkeypatch.setattr(seed, "SLEEP", 0)
    monkeypatch.setattr(seed, "BACKOFF", (0,))
    monkeypatch.setattr(seed, "RETRIES", 2)
    monkeypatch.setattr(seed, "CACHE", pathlib.Path("/tmp/photo-cache-test"))
    # A fresh URL each run: the disk cache is exactly what a retry builds on,
    # and a repeated key would answer from the previous run instead of the wire.
    url = f"https://example.test/retry-{uuid.uuid4()}"
    calls: list[int] = []

    def flaky(req, timeout=0):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                "https://example.test",
                429,
                "Too Many Requests",
                email.message.Message(),  # type: ignore[arg-type]
                io.BytesIO(b""),
            )
        return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr(seed.urllib.request, "urlopen", flaky)
    assert seed.http_json(url) == {"ok": True}
    assert len(calls) == 2


def test_a_dead_wiki_does_not_break_the_reseed(monkeypatch):
    """A hint can name any wiki; an unreachable one must not stop the pass."""
    import scripts.seed_photos as seed

    def boom(url: str) -> dict:
        raise OSError("no such wiki")

    monkeypatch.setattr(seed, "http_json", boom)
    assert seed.wikipedia_pageimage([("not-a-wiki", "Title")]) == {}


# ── What is never shown ─────────────────────────────────────────────────────


def test_a_point_without_a_url_has_no_photo():
    assert parse_photo({}) is None
    assert parse_photo({"photo_url": "   "}) is None


def test_a_photo_without_its_credit_is_dropped():
    # The licence requires the author and the licence name. Half a record is
    # worse than none: it would render a picture nobody is credited for.
    assert parse_photo({**FULL_ROW, "photo_author": ""}) is None
    assert parse_photo({**FULL_ROW, "photo_license": ""}) is None


def test_a_complete_record_survives():
    photo = parse_photo(FULL_ROW)
    assert photo == {
        "url": FULL_ROW["photo_url"],
        "author": "Александр Липилин",
        "license": "CC BY-SA 3.0",
        "source": FULL_ROW["photo_source"],
    }


def test_a_missing_file_page_does_not_kill_the_photo():
    photo = parse_photo({**FULL_ROW, "photo_source": ""})
    assert photo is not None
    assert photo["source"] is None


def test_place_accepts_a_photo_and_defaults_to_none():
    assert Place(id=1, name="x", category=None, lat=0.0, lon=0.0).photo is None
    place = Place(
        id=1,
        name="x",
        category=None,
        lat=0.0,
        lon=0.0,
        photo=Photo(url=FULL_ROW["photo_url"], author="A", license="CC0"),
    )
    assert place.photo is not None and place.photo.source is None


def test_an_itinerary_stop_carries_the_same_photo():
    row = {
        "id": 1,
        "source_url": "osm:node/1",
        "name": "x",
        "category": None,
        "town": None,
        "district": None,
        "lat": 0.0,
        "lon": 0.0,
        "visit_minutes": None,
        "opening_hours": None,
        "blurb": None,
        "fun_fact": None,
        "fun_facts": None,
        "links": None,
        "ticket_price": None,
        **FULL_ROW,
    }
    assert _stop_payload(row)["photo"]["author"] == "Александр Липилин"
    # A stop whose place has no photo says so instead of inventing one.
    assert _stop_payload({**row, "photo_url": None})["photo"] is None


# ── The shipped file ────────────────────────────────────────────────────────


def _photos() -> dict:
    with open(PHOTOS, encoding="utf-8") as fh:
        return json.load(fh)


def test_every_shipped_photo_is_creditable():
    photos = _photos()
    assert photos, "place_photos.json пуст — пересев не выполнялся"
    for key, photo in photos.items():
        assert photo["url"].startswith("https://"), f"{key}: не https"
        assert any(host in photo["url"] for host in WIKIMEDIA_HOSTS), (
            f"{key}: картинка не с Wikimedia — атрибуцию взять неоткуда"
        )
        assert photo["author"].strip(), f"{key}: без автора"
        assert photo["license"].strip(), f"{key}: без лицензии"
        assert photo["source"].startswith("http"), f"{key}: без страницы файла"
        assert photo["via"] in {"wikimedia_commons", "wikidata", "wikipedia"}
        assert photo["verified"] in {"image", "unknown"}


def test_shipped_keys_were_resolved_one_of_the_two_honest_ways():
    """Every photo must be traceable to either OSM or our own source link.

    An OSM-backed key has to exist among the hints we mined from the extract; a
    hand-authored one has to carry the article and the distance that got it in.
    A key that is neither would be a photo attached to nothing.
    """
    with open(HINTS, encoding="utf-8") as fh:
        hints = json.load(fh)

    for key, photo in _photos().items():
        prefix = key.split(":", 1)[0]
        if prefix in {"osm", "osm_poi"}:
            assert key.split(":", 1)[1] in hints, f"{key}: объекта нет среди фото-подсказок OSM"
            continue
        assert prefix in {"city", "region"}, key
        assert photo["via"] in {"wikidata", "wikipedia"}, key
        assert photo.get("article"), f"{key}: авторская точка без статьи"
        assert photo.get("match_m") is not None, f"{key}: без сверки координат"


def test_the_file_is_sorted_so_a_reseed_is_diffable():
    keys = list(_photos())
    assert keys == sorted(keys)


# ── Live ────────────────────────────────────────────────────────────────────


def _get(path: str):
    req = urllib.request.Request(BASE_URL + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


@pytest.mark.skipif(
    os.environ.get("SMOKE_SKIP_LIVE") == "1", reason="live-проверки отключены"
)
def test_live_itineraries_carry_a_credited_photo():
    try:
        payload = _get("/routes/itineraries")
    except (urllib.error.URLError, ConnectionError) as exc:
        pytest.skip(f"backend не отвечает на {BASE_URL}: {exc}")

    stops = [stop for item in payload["items"] for stop in item["stops"]]
    with_photo = [stop for stop in stops if stop.get("photo")]
    assert with_photo, "ни у одной остановки готовых маршрутов нет фото"

    for stop in with_photo:
        photo = stop["photo"]
        assert photo["url"] and photo["author"] and photo["license"], stop["name"]
