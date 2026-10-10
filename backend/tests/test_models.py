"""The row models must describe the schema, and only the schema.

`db/models` is a hand-written description of what Postgres stores — there is no
ORM and no autogeneration, so nothing keeps the two in step but a test. These
compare each model's fields against the columns the baseline actually creates,
in both directions: a column with no field is as much a defect as a field with no
column.

A generated column (`geom`, derived from `lat`/`lon` by Postgres) is deliberately
absent from its model, and is the only thing excluded.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any

import pytest

from db.models.area import Area
from db.models.client import Client, ClientPreferences
from db.models.place import Place, PlaceAlias, PlaceSource
from db.models.route import SavedRoute
from db.models.user import User, UserSession, VisitedPlace
from tests._schema import baseline_statements

# table → (model, columns the model deliberately does not carry)
MODELS: dict[str, tuple[type, set[str]]] = {
    "places": (Place, {"geom"}),
    "place_aliases": (PlaceAlias, set()),
    "place_sources": (PlaceSource, set()),
    "areas": (Area, {"geom"}),
    "users": (User, set()),
    "user_sessions": (UserSession, set()),
    "visited_places": (VisitedPlace, set()),
    "clients": (Client, set()),
    "client_preferences": (ClientPreferences, set()),
    "saved_routes": (SavedRoute, set()),
}

_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*$")
# A line whose first word is one of these is a table constraint, not a column.
_CONSTRAINT = {"PRIMARY", "UNIQUE", "CHECK", "CONSTRAINT", "FOREIGN", "EXCLUDE"}


def _create_table_body(table: str) -> str:
    """The parenthesised body of the baseline's `CREATE TABLE {table}`."""
    statement = next(
        s for s in baseline_statements()
        if re.search(rf"CREATE TABLE IF NOT EXISTS {table}\b", s)
    )
    return statement[statement.index("(") + 1 : statement.rindex(")")]


def _created_columns(table: str) -> set[str]:
    columns: set[str] = set()
    for line in _create_table_body(table).splitlines():
        line = line.strip().rstrip(",")
        if not line or line.startswith("--"):
            continue
        first = line.split()[0]
        if first.upper() in _CONSTRAINT or not _IDENTIFIER.match(first):
            continue
        columns.add(first)
    return columns


def _added_columns(table: str) -> set[str]:
    """Columns added by a later `ALTER TABLE ... ADD COLUMN`, e.g. `category_source`."""
    pattern = re.compile(
        rf"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS ([a-z_][a-z0-9_]*)"
    )
    return {match.group(1) for s in baseline_statements() for match in [pattern.search(s)] if match}


def _schema_columns(table: str) -> set[str]:
    return _created_columns(table) | _added_columns(table)


CASES = [(table, model, excluded) for table, (model, excluded) in MODELS.items()]


@pytest.mark.parametrize(("table", "model", "excluded"), CASES)
def test_model_matches_the_schema(table: str, model: type, excluded: set[str]) -> None:
    declared = {f.name for f in dataclasses.fields(model)}
    actual = _schema_columns(table) - excluded
    assert declared == actual, (
        f"{model.__name__} and the `{table}` table disagree — "
        f"only in the model: {sorted(declared - actual)}; "
        f"only in the schema: {sorted(actual - declared)}"
    )


def test_every_model_defaults_to_a_constructible_row() -> None:
    """A row model with no arguments must be valid: repositories fill it in field by field."""
    for _, (model, _excluded) in MODELS.items():
        built: Any = model()
        assert dataclasses.is_dataclass(built)


def test_an_unwritten_place_is_auto_owned() -> None:
    """`category_source` defaults to `auto`, which is what the trigger protects against."""
    assert Place().category_source == "auto"
