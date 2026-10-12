"""Choosing a provider is configuration, and it is read from the environment.

The rule worth pinning: one key is enough, an explicit choice wins, and a model
name has exactly one home. Nothing here reaches a network or a model.
"""

from __future__ import annotations

import pytest

from core.config import DEFAULT_PROVIDER, chosen_provider, model_name, provider, provider_names


@pytest.fixture(autouse=True)
def _no_ambient_llm_env(monkeypatch):
    """Start from nothing: the host's own keys must not decide these answers."""
    for name in provider_names():
        spec = provider(name)
        for env in (f"{name.upper()}_API_KEY", f"{name.upper()}_BASE_URL"):
            monkeypatch.delenv(env, raising=False)
        assert spec.default_model  # every provider names a default
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)


def test_the_only_key_present_decides(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert chosen_provider().name == "openrouter"


def test_an_explicit_choice_wins(monkeypatch) -> None:
    """`LLM_PROVIDER` is read before the key-based guess, even when it repeats it."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    assert chosen_provider().name == "openrouter"


def test_a_nonsense_choice_falls_back_rather_than_failing(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "not-a-provider")
    assert chosen_provider().name == "openrouter"


def test_with_no_key_at_all_the_default_is_named_but_has_none(monkeypatch) -> None:
    chosen = chosen_provider()
    assert chosen.name == DEFAULT_PROVIDER
    assert chosen.api_key is None


def test_the_model_name_has_one_home(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert model_name() == provider("openrouter").default_model

    monkeypatch.setenv("LLM_MODEL", "some/model")
    assert model_name() == "some/model"


def test_a_blank_variable_is_not_a_value(monkeypatch) -> None:
    """`.env` files carry empty assignments; they must mean "unset"."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("LLM_PROVIDER", "")
    assert chosen_provider().api_key is None
    assert model_name() == provider("openrouter").default_model


def test_the_provider_declares_where_its_settings_live(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://gateway.example/v1")
    assert provider("openrouter").base_url == "https://gateway.example/v1"


def test_every_provider_answers_to_the_openai_shape() -> None:
    """The vocabulary is one format with one client; a key that names a provider
    speaking a different one is a second client, not a second entry here."""
    assert provider_names() == ("openrouter",)
