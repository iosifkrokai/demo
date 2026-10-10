"""Local CPU embeddings — the ONE embedder for the agent and the seed.

E5 role prefixes: documents use ``"passage: "`` and queries ``"query: "``.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

MODEL_NAME = "intfloat/multilingual-e5-small"
EMBED_DIM = 384
QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "

MODEL_FILE = os.environ.get("FASTEMBED_MODEL_FILE", "onnx/model.onnx")

DB_BATCH = 64


class _State:
    """Module state holder (keeps module-level names patchable, no `global`)."""

    def __init__(self) -> None:
        self.model: Any = None
        self.registered = False
        self.available: bool | None = None


_state = _State()


def _build_model():
    """Register the custom-model description (once) and load it on the CPU."""
    from fastembed import TextEmbedding
    from fastembed.common.model_description import ModelSource, PoolingType

    if not _state.registered:
        TextEmbedding.add_custom_model(
            model=MODEL_NAME,
            pooling=PoolingType.MEAN,
            normalization=True,
            sources=ModelSource(hf=MODEL_NAME),
            dim=EMBED_DIM,
            model_file=MODEL_FILE,
        )
        _state.registered = True
    cache_dir = os.environ.get("FASTEMBED_CACHE_PATH") or None
    return TextEmbedding(
        model_name=MODEL_NAME,
        cache_dir=cache_dir,
        providers=["CPUExecutionProvider"],
    )


def _model_instance():
    if _state.model is None:
        _state.model = _build_model()
    return _state.model


def _embed_prefixed(prefixed: list[str]) -> list[list[float]]:
    """Embed already-prefixed texts. Returns plain float lists.

    The float coercion keeps pgvector honest (an int array silently mis-casts).
    """
    if not prefixed:
        return []
    model = _model_instance()
    return [[float(x) for x in vec] for vec in model.embed(prefixed)]


def embed_documents(texts: Sequence[str]) -> list[list[float]]:
    """Embed documents (places): every text gets the ``passage: `` prefix."""
    return _embed_prefixed([PASSAGE_PREFIX + t for t in texts])


def embed_text(text: str) -> list[float]:
    """Embed one piece of text, with the E5 ``query: `` prefix.

    This is the model itself. The entry point the pipeline calls is
    `embeddings.query.embed_query`, which puts the in-process cache in front of
    it; a warm-up or the seed wants the raw call and uses this one.
    """
    vectors = _embed_prefixed([QUERY_PREFIX + text])
    return vectors[0] if vectors else []


def is_available() -> bool:
    """True when the local model can be used (it is, once baked into the image)."""
    if _state.model is not None:
        return True
    if _state.available is not None:
        return _state.available
    try:
        _model_instance()
    except Exception:
        _state.available = False
    else:
        _state.available = True
    return _state.available


def embed_missing(places, *, batch: int = DB_BATCH) -> int:
    """Embed every row with ``embedding IS NULL``; returns how many were written.

    Computing the vectors is this module's job; storing them is the repository's,
    so the model layer never opens a transaction. Rows are keyed by id, so a
    re-run embeds nothing new.
    """
    pending = places.rows_missing_embedding()
    if not pending:
        return 0

    done = 0
    for i in range(0, len(pending), batch):
        chunk = pending[i:i + batch]
        texts = [f"{name}. {blurb or ''}" for _, name, blurb in chunk]
        vectors = embed_documents(texts)
        for (pid, _, _), vec in zip(chunk, vectors, strict=True):
            places.upsert_embedding(pid, vec)
        places.commit()
        done += len(chunk)
    return done
