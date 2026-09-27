"""Shared fixtures.

The reading cache is a module-level store keyed by the question, not by who
answers it. In a live process that is the point — the same question asked twice
should not be read twice — but inside a test run two tests can ask the same
question with *different* stubbed agents (one where a tool fails, one where it
does not), and the second test would then be handed the first one's answer. That
is not a cache being clever, it is one test leaking into another, so the store is
emptied before every test.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.planner import interpret_cache


@pytest.fixture(autouse=True)
def _empty_reading_cache():
    """No test starts with another test's reading in the store."""
    interpret_cache.INTERPRET_CACHE.clear()
    interpret_cache.EMBED_CACHE.clear()
    yield
    interpret_cache.INTERPRET_CACHE.clear()
    interpret_cache.EMBED_CACHE.clear()
