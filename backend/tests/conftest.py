"""The fixtures the three kinds of test share.

Two autouse fixtures keep every test honest: no test may reach a real model
provider, and no test may inherit another's cached reading or embedding. The
rest stand for a piece of the world a test is not allowed to touch: the model
provider (``fake_model``) and the running stack (``live_stack``).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import llm
from core import cache

BASE_URL = os.environ.get("GRODNO_BASE_URL", "http://localhost:8080")
HEALTH_TIMEOUT_S = 3.0

CACHES = (cache.INTERPRET_CACHE, cache.EMBED_CACHE)


@pytest.fixture(autouse=True)
def _no_real_model_requests(monkeypatch):
    """A test that reaches a real provider is a bug, not a slow test."""
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)


@pytest.fixture(autouse=True)
def _empty_caches():
    """No test starts with another test's reading or embedding."""
    for store in CACHES:
        store.clear()
    yield
    for store in CACHES:
        store.clear()


def fake_vector(text: str, dim: int = 384) -> list[float]:
    """A deterministic unit vector for a text — free, and stable across runs.

    It carries no meaning: ranking quality is the quality harness's question, and
    no unit test should be answering it by downloading a model.
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [((byte / 255.0) - 0.5) for byte in digest] * ((dim // len(digest)) + 1)
    vector = raw[:dim]
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


@pytest.fixture(autouse=True)
def _mocked_embedder(monkeypatch):
    """Vectorisation is mocked in every test.

    The real model is a download and an inference — slow, and beside the point:
    what a unit test can hold is the plumbing around it, not its judgement.
    """
    monkeypatch.setattr("embeddings.query.embed_query", fake_vector)
    monkeypatch.setattr("embeddings.model.is_available", lambda: True)
    monkeypatch.setattr("embeddings.model.embed_text", fake_vector)
    # `find_places` bound the name at import, so the binding is patched too.
    monkeypatch.setattr("agent.tools.places.embed_query", fake_vector)
    monkeypatch.setattr("agent.tools.places.embeddings_model.is_available", lambda: True)


class FakeModel:
    """A ``FunctionModel`` a test programs, one turn at a time.

    ``call`` queues a tool call the agent answers from its own tools; ``finish``
    queues the structured reading that ends the run. The queue is consumed one
    model turn per agent step, so the order written is the order played.
    """

    def __init__(self) -> None:
        self._steps: list[ModelResponse] = []

    def call(self, tool: str, /, **args: Any) -> FakeModel:
        self._steps.append(ModelResponse([ToolCallPart(tool, json.dumps(args))]))
        return self

    def finish(self, **fields: Any) -> FakeModel:
        self._steps.append(ModelResponse([ToolCallPart("final_result", json.dumps(fields))]))
        return self

    def _next(self, _messages: list[Any], _info: AgentInfo) -> ModelResponse:
        if not self._steps:
            raise AssertionError("fake_model ran out of scripted steps")
        return self._steps.pop(0)

    def build(self) -> FunctionModel:
        return FunctionModel(self._next, model_name="fake-agent")


@pytest.fixture
def fake_model(monkeypatch) -> FakeModel:
    """Replace the one provider seam with a programmable offline model.

    A key is set as well: ``agent.client.available`` refuses to run without one,
    so a replay that skipped this would never reach the model at all.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    fake = FakeModel()
    monkeypatch.setattr(llm, "make_model", fake.build)
    return fake


@dataclass(frozen=True)
class LiveStack:
    """The running stack: where it is, and what ``/health`` says it can do."""

    url: str
    health: dict[str, Any]

    def require(self, capability: str) -> None:
        """Skip the test when the stack cannot serve this capability.

        A skip is not a pass: it says the stack lacks the dependency, not that
        the endpoint was checked.
        """
        if not self.health.get(capability):
            pytest.skip(f"the stack at {self.url} reports {capability}=false in /health")


@pytest.fixture(scope="session")
def live_stack() -> LiveStack:
    """The live stack, or skip. ``/health`` is probed once for the whole session."""
    try:
        response = httpx.get(f"{BASE_URL}/health", timeout=HEALTH_TIMEOUT_S)
        response.raise_for_status()
        health = response.json()
    except Exception as exc:  # any failure means "not reachable"
        pytest.skip(f"no live stack at {BASE_URL} ({type(exc).__name__}: {exc})")
    return LiveStack(url=BASE_URL, health=health)


@pytest.fixture(scope="session")
def db_repos():
    """The real repositories, or skip when the database is unreachable.

    The tools are the layer under test, so they get the same bundle the service
    hands them — not a fake that could agree with a wrong assumption.
    """
    from db.store.areas import PostgresAreaRepository
    from db.store.places import PostgresPlaceRepository
    from db.store.registry import Repositories

    bundle = Repositories(places=PostgresPlaceRepository(), areas=PostgresAreaRepository())
    try:
        if not bundle.places.ping():
            pytest.skip("database not reachable")
    except Exception as exc:  # any storage failure is "not reachable" here
        pytest.skip(f"database not reachable: {type(exc).__name__}")
    yield bundle
    bundle.close()
