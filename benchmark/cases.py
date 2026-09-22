"""Benchmark suite for the Grodno route agent.

Run with: python -m benchmark.runner

Each case has a `query`, expected `categories`, and human-readable
`golden` description of what a good result should look like.
"""

from dataclasses import dataclass, field
from typing import Literal

Category = Literal[
    "замок", "дворец", "усадьба", "костёл", "церковь", "монастырь",
    "храм", "музей", "архитектура", "парк", "памятник",
    "инфраструктура", "кладбище",
]


@dataclass
class BenchmarkCase:
    query: str
    description: str  # what we're testing
    expected_categories: list[str] = field(default_factory=list)
    n_points: int = 4
    time_budget: int | None = None

    # Golden expectations (set by human review)
    golden_place_count: tuple[int, int] = (3, 5)  # min, max acceptable
    golden_categories: list[str] = field(default_factory=list)  # categories that MUST appear


CASES: list[BenchmarkCase] = [
    # ── TOP-NOTCH: well-formed queries that should work great ──
    BenchmarkCase(
        query="замки Гродно",
        description="замки — exact category match exists in DB",
        expected_categories=["замок"],
        golden_place_count=(2, 4),
        golden_categories=["замок"],
    ),
    BenchmarkCase(
        query="костёлы центра",
        description="костёлы — exact category, center bias via embeddings",
        expected_categories=["костёл"],
        golden_place_count=(3, 5),
        golden_categories=["костёл"],
    ),
    BenchmarkCase(
        query="дворцы Гродно",
        description="дворцы + усадьбы should return palaces",
        expected_categories=["дворец", "усадьба"],
        golden_place_count=(3, 5),
        golden_categories=["дворец"],
    ),
    BenchmarkCase(
        query="музеи Гродно",
        description="музеи — exact category",
        expected_categories=["музей"],
        golden_place_count=(3, 5),
        golden_categories=["музей"],
    ),

    # ── GOOD: queries with known LLM category extraction issues ──
    BenchmarkCase(
        query="парки и скверы",
        description="LLM often maps 'парки' to музей instead of парк",
        expected_categories=["парк"],
        golden_place_count=(2, 3),
        golden_categories=["парк"],
    ),
    BenchmarkCase(
        query="необычные памятники",
        description="LLM confuses 'памятники' with 'музей'",
        expected_categories=["памятник"],
        golden_place_count=(2, 4),
        golden_categories=["памятник"],
    ),
    BenchmarkCase(
        query="места съёмок белых рос",
        description="specific query — 'Памятная композиция Белые росы' should be first",
        expected_categories=["памятник", "монастырь", "архитектура"],
        golden_place_count=(3, 5),
        golden_categories=["памятник"],
    ),
    BenchmarkCase(
        query="неман и набережная",
        description="LLM maps 'неман' to парк — should return river-adjacent places",
        expected_categories=["парк", "инфраструктура"],
        golden_place_count=(2, 4),
        golden_categories=["парк", "инфраструктура"],
    ),

    # ── MEDIUM: broad or ambiguous queries ──
    BenchmarkCase(
        query="достопримечательности Гродно",
        description="broad 'sights' — any mix of categories ok if 4+ places",
        expected_categories=["монастырь", "дворец", "памятник", "храм", "архитектура"],
        golden_place_count=(4, 5),
        golden_categories=[],  # any is fine
    ),
    BenchmarkCase(
        query="история Гродно за один день",
        description="LLM parses 'история' as храм+инфраструктура instead of исторические места",
        expected_categories=["замок", "дворец", "монастырь", "костёл", "музей", "архитектура"],
        golden_place_count=(4, 6),
        golden_categories=["замок", "дворец", "монастырь", "костёл", "музей"],
    ),
    BenchmarkCase(
        query="религиозные места",
        description="LLM parses as церковь+музей+памятник — should include костёл too",
        expected_categories=["костёл", "церковь", "монастырь", "храм"],
        golden_place_count=(4, 6),
        golden_categories=["костёл", "церковь", "монастырь", "храм"],
    ),
    BenchmarkCase(
        query="усадьбы Гродненской области",
        description="'область' is fine — embeddings handle Grodno region",
        expected_categories=["усадьба", "дворец"],
        golden_place_count=(2, 4),
        golden_categories=["усадьба", "дворец"],
    ),
    BenchmarkCase(
        query="тихие места для прогулки",
        description="vague positive query — парк/усадьба/дворец preferred",
        expected_categories=["парк", "усадьба", "дворец"],
        golden_place_count=(3, 5),
        golden_categories=["парк", "усадьба", "дворец"],
    ),

    # ── EDGE CASES ──
    BenchmarkCase(
        query="всё о Гродно",
        description="maximum entropy query — LLM returns fallback, should still find 3+ places",
        expected_categories=[],  # fallback mode
        golden_place_count=(3, 5),
        golden_categories=[],
    ),
    BenchmarkCase(
        query="архитектура советского периода",
        description="should return Горисполком, Дворец культуры — not just pre-1900",
        expected_categories=["архитектура"],
        golden_place_count=(3, 5),
        golden_categories=["архитектура"],
    ),
    BenchmarkCase(
        query="горисполком",
        description="single specific building",
        expected_categories=["архитектура"],
        golden_place_count=(1, 3),
        golden_categories=["архитектура"],
    ),
    BenchmarkCase(
        query="замок",
        description="single word 'замок' — should return замки even with n=4",
        expected_categories=["замок"],
        golden_place_count=(2, 4),
        golden_categories=["замок"],
    ),
]
