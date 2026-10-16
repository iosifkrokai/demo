"""What `ticket_price` is allowed to mean: the values in the data, and the silences."""

from __future__ import annotations

import pytest

from core.prices import free_entry


@pytest.mark.parametrize(
    "text",
    [None, "", "   ", "—", "–", "-", "иногда", "по записи", "спросить на месте"],
)
def test_text_that_says_nothing_is_unknown(text: str | None) -> None:
    assert free_entry(text) is None


@pytest.mark.parametrize("text", ["no", "NO", " no ", "вход свободный", "бесплатно"])
def test_a_tag_or_a_phrase_says_free(text: str) -> None:
    assert free_entry(text) is True


def test_the_donation_form_of_free_is_still_free() -> None:
    assert free_entry("вход свободный (пожертвования)") is True


@pytest.mark.parametrize("text", ["yes", "5 BYN", "≈4 BYN", "20 BYN", "билеты 5–25 BYN"])
def test_a_price_or_the_tag_for_one_is_paid(text: str) -> None:
    assert free_entry(text) is False


def test_a_tariff_named_without_a_number_is_paid() -> None:
    assert free_entry("проход шлюза судном — по тарифам марины") is False


def test_a_discount_beside_a_price_is_still_a_price() -> None:
    assert free_entry("≈7 BYN, детям — скидки") is False
