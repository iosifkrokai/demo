"""Migration 0007 renames the transit-stop category, and only for auto rows."""

from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "db" / "migrations" / "0007_rename_transit_stop_category.sql"


def _update_block() -> str:
    """The UPDATE statement alone, with the comments stripped out."""
    sql = MIGRATION.read_text(encoding="utf-8")
    match = re.search(r"\bUPDATE\s+places\b.*?;", sql, re.DOTALL | re.IGNORECASE)
    assert match, "migration has no UPDATE places statement"
    body = "\n".join(line for line in match.group().splitlines() if not line.strip().startswith("--"))
    return body


def test_migration_exists_and_follows_the_numbering_style():
    assert MIGRATION.exists(), f"migration not found: {MIGRATION}"
    first_line = MIGRATION.read_text(encoding="utf-8").split("\n", 1)[0]
    assert re.match(r"--\s*\d{4}_", first_line), f"bad header: {first_line!r}"


def test_update_renames_the_old_code_to_the_new_one():
    body = _update_block()
    assert re.search(r"SET\s+category\s*=\s*'остановка транспорта'", body, re.IGNORECASE)
    assert re.search(r"category\s*=\s*'остановка'", body), (
        "the WHERE must keep the old value, or a re-run would not be a no-op"
    )


def test_update_is_restricted_to_auto_rows():
    """curated/dataset rows are reverted by places_guard_curated_category."""
    body = _update_block()
    assert re.search(r"category_source\s*=\s*'auto'", body), (
        "UPDATE must restrict to category_source = 'auto'"
    )


def test_the_guard_is_explained_where_it_is_relevant():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "places_guard_curated_category" in sql
