"""Embedding vectors must reach psycopg as floats, not as an int/float mix.

``agent.embeddings._embed_prefixed`` is annotated ``list[list[float]]``, but the
model's ``.embed`` can yield vectors whose per-dimension values land on exactly
``0`` or ``1`` and come back as Python ints. A list mixing int and float is
precisely what psycopg refuses to adapt (``DataError: cannot dump lists of mixed
types; got: float, int``), and because it depends on the values, the crash was
intermittent — the old OpenRouter-based seed embedded 920 of 1183 rows and then
died mid-run, leaving the seed half-done.

Every seed path now writes vectors with ``%s::vector`` through
``_embed_prefixed``, so these tests pin the coercion at that one seam and the
database cannot regress into a half-embedded state. The model is local now (no
OpenRouter, no API key): tests replace ``agent.embeddings._state.model`` with a
fake exposing ``.embed(list)``.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import embeddings

# A real embedding answers like this: 0 and 1 as ints, the rest as floats.
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
    # No model call, no crash: the seed's empty-batch path stays a no-op.
    assert embeddings._embed_prefixed([]) == []


def test_the_mixed_payload_really_is_what_psycopg_rejects() -> None:
    """Pin the reason, not just the symptom: ints and floats together.

    If this ever stops being true the coercion is harmless, but the test then says
    so explicitly instead of quietly testing nothing.
    """
    assert any(isinstance(x, int) for x in MIXED)
    assert any(isinstance(x, float) for x in MIXED)
    assert {float(x) for x in MIXED} == {0.0, 0.0123, 1.0, -1.0, 0.5, 2.0}
