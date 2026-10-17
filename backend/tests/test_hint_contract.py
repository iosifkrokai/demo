"""The ask box's starters are the benchmark's own requests, and must not drift from them.

The chips are not worded here; they are read from the measured cases and walks.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from quality.bench.cases import load_curated_routes, load_golden_cases

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n"
CHIPS = 22
CHIPS_REFINE = 6
REFINE_FROM_BENCHMARK = "добавь кофейню и туалет"


ENTRY = re.compile(r"(\w+)\s*:\s*(['\"])((?:[^\\]|\\.)*?)\2")


def _block_body(text: str, start: int) -> str:
    """The text between a block's braces, quoting and nesting respected."""
    depth = 1
    quote = ""
    i = start
    while i < len(text):
        char = text[i]
        if quote:
            if char == "\\":
                i += 2
                continue
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:i]
        i += 1
    raise AssertionError("a dictionary block is not closed")


def _block(text: str, name: str) -> dict[str, str]:
    """One `ask.<name>` block of a dictionary, entries only, or a loud failure."""
    start = re.search(rf"\b{name}\s*:\s*\{{", text)
    if start is None:
        raise AssertionError(f"no {name} block in the dictionary")
    body = _block_body(text, start.end())
    rest = ENTRY.sub("", body)
    if rest.strip(" \t\n,"):
        left = re.sub(r"[\s,]+", " ", rest).strip()
        raise AssertionError(f"{name}: the reader left {left[:90]!r} unread")
    return {
        key: value.replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")
        for key, _quote, value in ENTRY.findall(body)
    }


def _ask(locale: str) -> tuple[dict[str, str], dict[str, str]]:
    path = FRONTEND / f"{locale}.ts"
    if not path.exists():
        pytest.skip("the frontend tree is not here — the hints and the cases cannot be compared")
    text = path.read_text(encoding="utf-8")
    return _block(text, "chips"), _block(text, "chipsRefine")


def test_the_parsers_see_every_chip() -> None:
    """A value the reader misses would make the comparison below pass vacuously."""
    for locale in ("ru", "en"):
        chips, refine = _ask(locale)
        assert len(chips) == CHIPS, f"{locale}: {len(chips)} base chips, expected {CHIPS}"
        assert len(refine) == CHIPS_REFINE, (
            f"{locale}: {len(refine)} refine chips, expected {CHIPS_REFINE}"
        )


def test_the_russian_starters_are_the_requests_the_benchmark_measures() -> None:
    """The chips and the measured requests are the same set — no invented wording,
    and no case left without a way to be tapped. A case naming a moment (`filters.at`)
    is the exception: a chip carries text and a transport, not an hour."""
    measured = {
        case.query
        for case in load_golden_cases()
        if case.locale == "ru" and not case.filters.get("at")
    }
    measured |= {route.query_ru for route in load_curated_routes()}

    chips, _refine = _ask("ru")
    offered = set(chips.values())

    assert offered - measured == set(), "a chip is not the request any case measures"
    assert measured - offered == set(), "a measured request is missing from the chips"


def test_the_english_starters_carry_the_measured_english_requests() -> None:
    """Only two requests are measured in English; the rest of the row is written here,
    so the pair that IS measured has to travel word for word."""
    measured = {case.query for case in load_golden_cases() if case.locale == "en"}

    chips, _refine = _ask("en")
    assert measured <= set(chips.values())
    assert all(text.strip() for text in chips.values())


def test_the_refine_row_carries_the_benchmark_instruction() -> None:
    for locale in ("ru", "en"):
        chips, refine = _ask(locale)
        assert len(set(refine.values())) == CHIPS_REFINE, f"{locale}: a refine chip repeats"
        assert not set(chips) & set(refine), "a chip key cannot be in both rows"

    _chips, ru_refine = _ask("ru")
    assert REFINE_FROM_BENCHMARK in ru_refine.values()
