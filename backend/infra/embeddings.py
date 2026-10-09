"""Local CPU embeddings — the ONE embedder for the agent and the seed.

Retrieval used to call OpenRouter in five places (four seed scripts plus
``planner.pipeline._openrouter_embed``). They are all replaced by this module:
``intfloat/multilingual-e5-small`` (384-d) runs locally through fastembed/ONNX on
the CPU, so embeddings need no API key and the vector signal never degrades.

E5 models are trained with role prefixes: documents must be embedded with
``"passage: "`` and queries with ``"query: "``. There is no public entry point
that skips the prefixing — :func:`embed_documents` and :func:`embed_query` are
the only ways in, so a caller cannot swap the two conventions by accident.

Tests never load the real model (that would download hundreds of MB): they
replace ``_state.model`` with a fake exposing ``.embed(list) -> iterable``.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

MODEL_NAME = "intfloat/multilingual-e5-small"
EMBED_DIM = 384
QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "

# The ONNX file baked into the image. fp32 (onnx/model.onnx, ~470 MB) is the most
# portable; the build may point at onnx/model_O4.onnx or the quantized variant.
MODEL_FILE = os.environ.get("FASTEMBED_MODEL_FILE", "onnx/model.onnx")

# How many rows to embed per DB round-trip in embed_missing.
DB_BATCH = 64


class _State:
    """Module state holder (keeps module-level names patchable, no `global`)."""

    def __init__(self) -> None:
        # A fastembed TextEmbedding in production; tests swap in a fake exposing
        # `.embed(list)`. Typed `Any` rather than a Protocol because fastembed's
        # `embed` signature (batch_size/parallel/**kwargs) does not structurally
        # match a narrow protocol.
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

    This is the single seam every caller (and every test) goes through; the
    float coercion keeps pgvector honest (an int array silently mis-casts).
    """
    if not prefixed:
        return []
    model = _model_instance()
    return [[float(x) for x in vec] for vec in model.embed(prefixed)]


def embed_documents(texts: Sequence[str]) -> list[list[float]]:
    """Embed documents (places): every text gets the ``passage: `` prefix."""
    return _embed_prefixed([PASSAGE_PREFIX + t for t in texts])


def embed_query(text: str) -> list[float]:
    """Embed a search query: the text gets the ``query: `` prefix."""
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


def embed_missing(conn, *, batch: int = DB_BATCH) -> int:
    """Embed every row with ``embedding IS NULL``; returns how many were written.

    The seed calls this after the upsert. Rows are keyed by id, so a re-run
    embeds nothing new.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, blurb FROM places WHERE embedding IS NULL ORDER BY id")
        pending = cur.fetchall()
    if not pending:
        return 0

    done = 0
    for i in range(0, len(pending), batch):
        chunk = pending[i:i + batch]
        texts = [f"{name}. {blurb or ''}" for _, name, blurb in chunk]
        vectors = embed_documents(texts)
        with conn.cursor() as cur:
            for (pid, _, _), vec in zip(chunk, vectors, strict=True):
                cur.execute(
                    "UPDATE places SET embedding = %s::vector WHERE id = %s",
                    (str(vec), pid),
                )
        conn.commit()
        done += len(chunk)
    return done
