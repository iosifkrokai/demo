"""Shared fixtures.

The reading cache is emptied before every test so one test cannot leak into
another. ``fake_llm`` stands in for OpenRouter: the real agent path runs — prompt,
tools, structured output — against a canned reading, with no network.
"""

from __future__ import annotations

import json
import os
import sys

import pytest
from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import llm as model
from core import cache as interpret_cache


@pytest.fixture(autouse=True)
def _empty_reading_cache():
    """No test starts with another test's reading in the store."""
    interpret_cache.INTERPRET_CACHE.clear()
    interpret_cache.EMBED_CACHE.clear()
    yield
    interpret_cache.INTERPRET_CACHE.clear()
    interpret_cache.EMBED_CACHE.clear()


@pytest.fixture(autouse=True)
def _no_real_model_requests(monkeypatch):
    """A test that reaches a real provider is a bug, not a slow test."""
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)


class FakeLLM:
    """The reading every request gets until a test says otherwise."""

    def __init__(self) -> None:
        self.payload: dict = {}

    def set(self, **fields) -> None:
        """The fields the model will report, as the AgentReading they map to."""
        self.payload = fields

    def model(self) -> FunctionModel:
        def fn(messages, info: AgentInfo) -> ModelResponse:
            return ModelResponse(
                [ToolCallPart("final_result", json.dumps(self.payload))]
            )

        return FunctionModel(fn, model_name="fake-interpret")


@pytest.fixture
def fake_llm(monkeypatch) -> FakeLLM:
    """A key and a model that answer without OpenRouter.

    Patches the one seam ``agent.model`` documents (``make_model``).
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    fake = FakeLLM()
    monkeypatch.setattr(model, "make_model", fake.model)
    return fake
