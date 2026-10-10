"""The local embedder: prefixes, float coercion, and schema-drift guard.

A fake whose ``.embed`` records its inputs stands in for fastembed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from embeddings import model
from tests._schema import baseline_sql

BACKEND = Path(__file__).resolve().parents[1]


class _FakeModel:
    def __init__(self, vectors: list[list[float]] | None = None) -> None:
        self._vectors = vectors
        self.inputs: list[list[str]] = []

    def embed(self, texts):
        self.inputs.append(list(texts))
        if self._vectors is not None:
            return self._vectors
        return [[0.5] * model.EMBED_DIM for _ in texts]


@pytest.fixture
def fake(monkeypatch):
    onnx = _FakeModel()
    monkeypatch.setattr(model._state, "model", onnx, raising=False)
    monkeypatch.setattr(model._state, "available", None, raising=False)
    yield onnx


def test_documents_get_the_passage_prefix(fake):
    model.embed_documents(["Старый замок", "Костёл"])
    assert fake.inputs[0] == ["passage: Старый замок", "passage: Костёл"]


def test_query_gets_the_query_prefix(fake):
    model.embed_text("замки Гродно")
    assert fake.inputs[0] == ["query: замки Гродно"]


def test_int_vectors_are_coerced_to_float(monkeypatch):
    monkeypatch.setattr(model._state, "model", _FakeModel([[1, 2, 3]]), raising=False)
    out = model._embed_prefixed(["passage: x"])
    assert out == [[1.0, 2.0, 3.0]]
    assert all(isinstance(v, float) for v in out[0])


def test_embed_query_returns_a_plain_vector(fake):
    assert len(model.embed_text("проверка")) == model.EMBED_DIM


def test_available_is_true_when_a_model_is_installed(fake):
    assert model.is_available() is True


def test_embedding_dim_matches_the_schema():
    """Drift guard: the model dim and the `vector(N)` columns must agree."""
    def dims(sql: str) -> set[int]:
        return {int(n) for n in re.findall(r"vector\(\s*(\d+)\s*\)", sql, re.IGNORECASE)}

    schema = baseline_sql()
    assert model.EMBED_DIM in dims(schema), "the schema dim drifted from EMBED_DIM"
