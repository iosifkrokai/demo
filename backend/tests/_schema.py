"""The schema as SQL, read from the Alembic baseline.

Tests used to read the loose files under `db/`; the schema now lives in one
Alembic revision, so they render that instead of a file that no longer exists.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
BASELINE = BACKEND / "alembic" / "versions" / "0001_baseline.py"


def baseline_statements() -> list[str]:
    """Every statement the baseline runs, in order."""
    spec = importlib.util.spec_from_file_location("grodno_baseline", BASELINE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    seen: list[str] = []

    class _Recorder:
        def execute(self, sql: Any, *args: Any, **kwargs: Any) -> None:
            seen.append(str(sql))

    module.op = _Recorder()
    module.upgrade()
    return seen


def baseline_sql() -> str:
    """The same statements as one script, for a single multi-statement execute."""
    return ";\n".join(baseline_statements()) + ";"
