"""Shared fixtures.

The reading cache is emptied before every test so one test cannot leak into another.
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
