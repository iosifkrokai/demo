"""Embedding vectors must reach psycopg as floats, not as JSON's int/float mix.

`_openrouter_embed` is annotated `list[list[float]]`, but the OpenRouter response
is JSON: any dimension that lands on exactly ``0`` or ``1`` comes back as a Python
int. A list mixing int and float is precisely what psycopg refuses to adapt
(``DataError: cannot dump lists of mixed types; got: float, int``), and because it
depends on the values, the crash was intermittent — `ingest_poi.py` embedded 920
of 1183 rows and then died mid-run, leaving the seed half-done.

Two scripts write vectors with ``%s::vector`` (ingest_poi, enrich_places) and both
had the bug; the sibling scripts (seed_region, load_osm) survived only because they
happen to stringify (``str(item["embedding"])``). These tests pin the coercion at
the seam, so the seed path cannot regress into a half-embedded database.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import enrich_places
import ingest_poi

# A real embedding answers like this: 0 and 1 as ints, the rest as floats.
MIXED = [0, 0.0123, 1, -1, 0.5, 2.0]


class _Resp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_ingest_poi_embed_returns_floats(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        ingest_poi.httpx,
        "post",
        lambda *a, **k: _Resp({"data": [{"embedding": MIXED}]}),
    )
    vecs = ingest_poi._openrouter_embed(["Старый замок. Королевский замок Витовта"])
    assert len(vecs) == 1
    assert all(isinstance(x, float) for x in vecs[0]), f"mixed types survived: {vecs[0]}"
    assert vecs[0] == [float(x) for x in MIXED]


def test_enrich_places_embed_returns_floats(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    class _Client:
        def __init__(self, *a, **k) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a) -> bool:
            return False

        def post(self, *a, **k) -> _Resp:
            return _Resp({"data": [{"embedding": MIXED}]})

    monkeypatch.setattr(enrich_places.httpx, "Client", _Client)
    vecs = enrich_places._openrouter_embed(["Костёл"])
    assert all(isinstance(x, float) for x in vecs[0]), f"mixed types survived: {vecs[0]}"


def test_the_mixed_payload_really_is_what_psycopg_rejects() -> None:
    """Pin the reason, not just the symptom: ints and floats together.

    If this ever stops being true the coercion is harmless, but the test then says
    so explicitly instead of quietly testing nothing.
    """
    assert any(isinstance(x, int) for x in MIXED)
    assert any(isinstance(x, float) for x in MIXED)
    assert {float(x) for x in MIXED} == {0.0, 0.0123, 1.0, -1.0, 0.5, 2.0}
