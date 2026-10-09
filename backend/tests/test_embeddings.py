"""The local embedder: prefixes, float coercion, and schema-drift guard.

No model is loaded here — the real one would download hundreds of MB. A fake
whose ``.embed`` records its inputs stands in for fastembed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent import embeddings

BACKEND = Path(__file__).resolve().parents[1]


class _FakeModel:
    def __init__(self, vectors: list[list[float]] | None = None) -> None:
        self._vectors = vectors
        self.inputs: list[list[str]] = []

    def embed(self, texts):
        self.inputs.append(list(texts))
        if self._vectors is not None:
            return self._vectors
        return [[0.5] * embeddings.EMBED_DIM for _ in texts]


@pytest.fixture
def fake(monkeypatch):
    model = _FakeModel()
    monkeypatch.setattr(embeddings._state, "model", model, raising=False)
    monkeypatch.setattr(embeddings._state, "available", None, raising=False)
    yield model


def test_documents_get_the_passage_prefix(fake):
    embeddings.embed_documents(["Старый замок", "Костёл"])
    assert fake.inputs[0] == ["passage: Старый замок", "passage: Костёл"]


def test_query_gets_the_query_prefix(fake):
    embeddings.embed_query("замки Гродно")
    assert fake.inputs[0] == ["query: замки Гродно"]


def test_int_vectors_are_coerced_to_float(monkeypatch):
    # pgvector silently mis-casts an int array; the seam must force floats.
    monkeypatch.setattr(embeddings._state, "model", _FakeModel([[1, 2, 3]]), raising=False)
    out = embeddings._embed_prefixed(["passage: x"])
    assert out == [[1.0, 2.0, 3.0]]
    assert all(isinstance(v, float) for v in out[0])


def test_embed_query_returns_a_plain_vector(fake):
    assert len(embeddings.embed_query("проверка")) == embeddings.EMBED_DIM


def test_available_is_true_when_a_model_is_installed(fake):
    assert embeddings.is_available() is True


def test_embedding_dim_matches_the_schema():
    """Drift guard: the model dim and the `vector(N)` columns must agree."""
    def dims(sql: str) -> set[int]:
        return {int(n) for n in re.findall(r"vector\(\s*(\d+)\s*\)", sql, re.IGNORECASE)}

    init = (BACKEND / "db" / "init.sql").read_text(encoding="utf-8")
    assert embeddings.EMBED_DIM in dims(init), "init.sql embedding dim drifted from EMBED_DIM"

    migrations = sorted((BACKEND / "db" / "migrations").glob("*.sql"))
    assert migrations, "no migrations found"
    last = migrations[-1].read_text(encoding="utf-8")
    if dims(last):
        assert embeddings.EMBED_DIM in dims(last)
