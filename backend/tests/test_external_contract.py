import csv
import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "db" / "data"
CONTRACT = DATA / "external" / "v1"


def _schema() -> dict:
    return json.loads((CONTRACT / "schema.json").read_text(encoding="utf-8"))


def _example() -> dict:
    return json.loads((CONTRACT / "example.json").read_text(encoding="utf-8"))


def _property_enums(node: object, name: str = "", table: dict[str, set[str]] | None = None):
    """Every value a named property is allowed to take, across the whole schema."""
    table = {} if table is None else table
    if isinstance(node, dict):
        values = node.get("enum")
        if name and isinstance(values, list):
            table.setdefault(name, set()).update(str(v) for v in values)
        for key, value in node.items():
            _property_enums(value, key, table)
    elif isinstance(node, list):
        for value in node:
            _property_enums(value, name, table)
    return table


def _walk(node: object, path: str = ""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, key)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value, path)
    else:
        yield path, node


def test_the_example_only_uses_values_the_schema_allows() -> None:
    allowed = _property_enums(_schema())
    for key, value in _walk(_example()):
        if key in allowed and isinstance(value, str):
            assert value in allowed[key], f"{key}: {value!r} is not in the enum"


def test_every_dictionary_value_is_a_value_the_schema_accepts() -> None:
    legend = "Да / Нет"
    schema = _schema()
    allowed = _property_enums(schema)
    reachable = {value for values in allowed.values() for value in values}
    for name, values in schema["x-dictionaries"].items():
        if name == legend:
            continue
        for value in values:
            assert value in reachable, f"{name}: {value!r} is listed but no enum accepts it"


def test_the_form_carries_every_field_of_every_definition() -> None:
    from db import external_form

    schema = _schema()
    covered = {
        (plan["def"], column["path"][0])
        for plan in external_form.PLANS
        for column in external_form._columns(schema, plan)
    }
    for plan in external_form.PLANS:
        if plan["def"] in schema["$defs"]["place"]["properties"]:
            covered.add(("place", plan["def"]))
    for name, definition in schema["$defs"].items():
        for prop in definition["properties"]:
            assert (name, prop) in covered, f"{name}.{prop} не попало в форму"


def test_the_form_carries_the_document_header() -> None:
    from db import external_form

    schema = _schema()
    fields = {name for name, _, _ in external_form.DOCUMENT_FIELDS}
    assert fields == {"version", "sent_on", "sender"}
    from_plans = {plan["def"] for plan in external_form.PLANS}
    assert set(schema["properties"]) - fields <= from_plans | {"places"}


def test_the_place_categories_are_ones_the_product_stores() -> None:
    schema = _schema()
    lines = [
        line
        for line in (DATA / "taxonomy.csv").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    codes = {row["code"] for row in csv.DictReader(lines)}
    for category in schema["$defs"]["place"]["properties"]["category"]["enum"]:
        assert category in codes, f"{category!r} is not a taxonomy code"
