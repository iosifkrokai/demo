"""The model endpoint is configuration, read from the environment.

No network and no model: a blank assignment means "unset", not a value."""

from __future__ import annotations

import pytest

from core.config import chosen_provider, model_name


@pytest.fixture(autouse=True)
def _no_ambient_llm_env(monkeypatch):
    """Start from nothing: the host's own keys must not decide these answers."""
    for env in ("OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(env, raising=False)


def test_the_settings_come_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://gateway.example/v1")
    assert chosen_provider().base_url == "https://gateway.example/v1"
    assert chosen_provider().default_model


def test_the_key_is_read_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert chosen_provider().name == "openrouter"
    assert chosen_provider().api_key == "k"


def test_with_no_key_at_all_the_endpoint_is_named_but_has_none(monkeypatch) -> None:
    chosen = chosen_provider()
    assert chosen.name == "openrouter"
    assert chosen.api_key is None


def test_the_model_name_has_one_home(monkeypatch) -> None:
    assert model_name() == chosen_provider().default_model

    monkeypatch.setenv("LLM_MODEL", "some/model")
    assert model_name() == "some/model"


def test_a_blank_variable_is_not_a_value(monkeypatch) -> None:
    """`.env` files carry empty assignments; they must mean "unset"."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "")
    assert chosen_provider().api_key is None
    assert model_name() == chosen_provider().default_model
