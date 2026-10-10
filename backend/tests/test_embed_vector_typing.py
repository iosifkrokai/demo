"""Embedding vectors must reach psycopg as floats, not as an int/float mix.

The model can yield int-valued dims; psycopg refuses a list of mixed types.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from infra import embeddings

MIXED = [0, 0.0123, 1, -1, 0.5, 2.0]


class _FakeModel:
    """Stands in for the ONNX model: returns the vectors it is handed verbatim."""

    def __init__(self, vectors: list[list]) -> None:
        self._vectors = vectors

    def embed(self, texts: list[str]):
        return self._vectors


def test_embed_prefixed_coerces_model_output_to_floats(monkeypatch) -> None:
    monkeypatch.setattr(embeddings._state, "model", _FakeModel([MIXED]))
    vecs = embeddings._embed_prefixed(["passage: Старый замок. Королевский замок Витовта"])
    assert len(vecs) == 1
    assert all(isinstance(x, float) for x in vecs[0]), f"mixed types survived: {vecs[0]}"
    assert vecs[0] == [float(x) for x in MIXED]


def test_embed_prefixed_returns_nothing_for_no_texts() -> None:
    assert embeddings._embed_prefixed([]) == []


def test_the_mixed_payload_really_is_what_psycopg_rejects() -> None:
    """Pin the reason, not just the symptom: ints and floats together.

    If this stops being true the coercion is harmless, and the test then says so.
    """
    assert any(isinstance(x, int) for x in MIXED)
    assert any(isinstance(x, float) for x in MIXED)
    assert {float(x) for x in MIXED} == {0.0, 0.0123, 1.0, -1.0, 0.5, 2.0}
