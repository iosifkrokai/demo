"""Tests for scripts/data_inventory.py.

Everything here is offline: the SQL builders are pinned as text, the report
collector is driven by a fake psycopg connection, and the text renderer is
pinned against a synthetic report.  The single live test hits the real database
through the backend's own DSN (agent.config settings, i.e. DATABASE_URL) and
skips cleanly when Postgres is not reachable.

Run from backend/:

    ../../.venv/bin/python -m pytest tests/test_data_inventory.py -v
    # or with the worktree venv:
    /workspaces/demo/backend/.venv/bin/python -m pytest tests/test_data_inventory.py -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

import psycopg
import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)

from agent.config import settings  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "data_inventory", os.path.join(BACKEND, "scripts", "data_inventory.py")
)
data_inventory = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(data_inventory)

# ── Fake psycopg connection ──────────────────────────────────────────────────


class FakeCursor:
    """Records execute() calls and serves one queued result set per call."""

    def __init__(self, results: list[list[tuple]]):
        self._results = list(results)
        self.calls: list[tuple[str, dict | None]] = []
        self._rows: list[tuple] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, query, params=None) -> None:
        self.calls.append((str(query), params))
        self._rows = self._results.pop(0)

    def fetchone(self) -> tuple:
        return self._rows[0]

    def fetchall(self) -> list[tuple]:
        return list(self._rows)


class FakeConnection:
    def __init__(self, results: list[list[tuple]]):
        self._cursor = FakeCursor(results)

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def cursor(self) -> FakeCursor:
        return self._cursor


# One result set per collect_report() query, in order: overview, top
# categories, distinct categories, client table names, then one count per table.
FAKE_RESULTS = [
    [(3712, 3710, 2, 646, 2530, 2530, 7)],  # places overview
    [("архитектура", 1252), ("кафе", 593)],  # top categories
    [(57,)],  # distinct categories
    [("client_preferences",), ("clients",), ("saved_routes",)],  # table names
    [(1,)],  # count: client_preferences
    [(2,)],  # count: clients
    [(1,)],  # count: saved_routes
]

EXPECTED_REPORT = {
    "places": {
        "total": 3712,
        "with_category": 3710,
        "without_category": 2,
        "without_coordinates": 2,
        "with_opening_hours": 646,
        "with_fun_facts": 2530,
        "with_links": 2530,
        "outside_grodno_bbox": 7,
        "bbox": [23.49, 52.72, 26.73, 55.03],
        "distinct_categories": 57,
        "top_categories": [
            {"category": "архитектура", "count": 1252},
            {"category": "кафе", "count": 593},
        ],
    },
    "client_tables": [
        {"table": "client_preferences", "rows": 1},
        {"table": "clients", "rows": 2},
        {"table": "saved_routes", "rows": 1},
    ],
}


# ── SQL builders ─────────────────────────────────────────────────────────────


def test_places_overview_sql_pins_the_required_counts() -> None:
    query = data_inventory.places_overview_sql()
    assert "FROM places" in query
    for alias in (
        "with_category",
        "without_coordinates",
        "with_opening_hours",
        "with_fun_facts",
        "with_links",
        "outside_grodno_bbox",
    ):
        assert f"AS {alias}" in query
    # The bbox test is parameterised, not inlined.
    for param in ("min_lon", "min_lat", "max_lon", "max_lat"):
        assert f"%({param})s" in query


def test_category_counts_sql_groups_and_sorts_stably() -> None:
    query = data_inventory.category_counts_sql()
    assert "GROUP BY category" in query
    assert "ORDER BY n DESC, category ASC" in query
    assert "%(limit)s" in query


def test_client_tables_sql_discovers_by_schema_and_prefix() -> None:
    query = data_inventory.client_tables_sql()
    assert "information_schema.tables" in query
    assert "table_schema" in query
    assert "client" in query
    assert "ORDER BY" in query  # stable order regardless of the catalog


def test_table_count_sql_quotes_the_identifier() -> None:
    composed = data_inventory.table_count_sql("clients")
    assert "count(*)" in str(composed)
    assert "clients" in str(composed)


# ── Collector over a mock connection ─────────────────────────────────────────


def test_collect_report_runs_every_query_without_errors() -> None:
    conn = FakeConnection([list(rows) for rows in FAKE_RESULTS])
    report = data_inventory.collect_report(conn)
    assert report == EXPECTED_REPORT
    # Seven queries were assembled and executed: overview, top categories,
    # distinct count, table discovery, then one count per discovered table.
    assert len(conn._cursor.calls) == 7
    for query, _params in conn._cursor.calls:
        assert isinstance(query, str) and query.strip()


# ── Pure helpers ─────────────────────────────────────────────────────────────


def test_is_outside_bbox() -> None:
    # Grodno old town: inside the frame.
    assert data_inventory.is_outside_bbox(53.6771, 23.8290) is False
    # Minsk: outside.
    assert data_inventory.is_outside_bbox(53.9006, 27.5590) is True
    # On the frame edge: still inside (the SQL uses strict < / >).
    assert data_inventory.is_outside_bbox(52.72, 23.49) is False
    assert data_inventory.is_outside_bbox(52.7199, 23.49) is True
    # Unknown coordinates are not "outside" — same semantics as the SQL FILTER.
    assert data_inventory.is_outside_bbox(None, 23.8290) is False
    assert data_inventory.is_outside_bbox(53.6771, None) is False


# ── Text renderer (pinned order and alignment) ───────────────────────────────

EXPECTED_TEXT = """\
places: 3712 total
  with category           3710
  without category        2
  without coordinates     2
  with opening_hours      646
  with fun_facts          2530
  with links              2530
  outside grodno_bbox     7
    bbox is a rough frame, not the oblast boundary: lon 23.49..26.73, lat 52.72..55.03

categories: top 10 of 57 distinct
    1252  архитектура
     593  кафе

client tables
  client_preferences      1
  clients                 2
  saved_routes            1\
"""


def test_render_report_is_stable_and_complete() -> None:
    assert data_inventory.render_report(EXPECTED_REPORT) == EXPECTED_TEXT


def test_render_report_without_client_tables() -> None:
    report = {
        "places": {
            "total": 0,
            "with_category": 0,
            "without_category": 0,
            "without_coordinates": 0,
            "with_opening_hours": 0,
            "with_fun_facts": 0,
            "with_links": 0,
            "outside_grodno_bbox": 0,
            "bbox": [23.49, 52.72, 26.73, 55.03],
            "distinct_categories": 0,
            "top_categories": [],
        },
        "client_tables": [],
    }
    text = data_inventory.render_report(report)
    assert "places: 0 total" in text
    assert "categories: top 10 of 0 distinct" in text
    assert "client tables\n  none" in text


# ── main(): both output modes over a mock connection ────────────────────────


def _run_main(monkeypatch, capsys, argv: list[str]) -> dict:
    monkeypatch.setattr(data_inventory.psycopg, "connect", lambda dsn: FakeConnection(FAKE_RESULTS))
    exit_code = data_inventory.main(argv)
    assert exit_code == 0
    return json.loads(capsys.readouterr().out)


def test_main_json_mode(monkeypatch, capsys) -> None:
    payload = _run_main(monkeypatch, capsys, ["--json"])
    assert payload == EXPECTED_REPORT


def test_main_text_mode_matches_renderer(monkeypatch, capsys) -> None:
    monkeypatch.setattr(data_inventory.psycopg, "connect", lambda dsn: FakeConnection(FAKE_RESULTS))
    assert data_inventory.main([]) == 0
    out = capsys.readouterr().out
    assert out == EXPECTED_TEXT + "\n"


def test_main_reports_connection_failure(monkeypatch, capsys) -> None:
    def _refuse(dsn):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(data_inventory.psycopg, "connect", _refuse)
    assert data_inventory.main([]) == 2
    err = capsys.readouterr().err
    assert "cannot connect" in err


# ── The one live check: real database, skipped when unreachable ─────────────


def test_live_database_report() -> None:
    try:
        conn = psycopg.connect(settings.DSN, connect_timeout=3)
    except psycopg.OperationalError as exc:
        pytest.skip(f"database not reachable via agent.config DSN: {exc}")
    with conn:
        report = data_inventory.collect_report(conn)

    p = report["places"]
    assert p["total"] >= 0
    # Internal consistency of the overview counts.
    assert p["with_category"] + p["without_category"] == p["total"]
    for key in (
        "without_coordinates",
        "with_opening_hours",
        "with_fun_facts",
        "with_links",
        "outside_grodno_bbox",
    ):
        assert 0 <= p[key] <= p["total"]
    assert len(p["top_categories"]) <= data_inventory.CATEGORY_TOP_N
    assert p["distinct_categories"] >= len(p["top_categories"])
    assert report["client_tables"] == sorted(
        report["client_tables"], key=lambda entry: entry["table"]
    )
    for entry in report["client_tables"]:
        assert entry["table"].startswith("client")
        assert entry["rows"] >= 0
