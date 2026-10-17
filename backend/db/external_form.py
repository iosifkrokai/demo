"""The Excel form a sender fills, built from the reply's JSON Schema and read back into it.
The schema in ``db/data/external/v1/schema.json`` stays the contract; run with
``uv run --with openpyxl --with jsonschema python -m db.external_form build``."""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

CONTRACT = Path(__file__).resolve().parent / "data" / "external" / "v1"
SCHEMA_PATH = CONTRACT / "schema.json"
EXAMPLE_PATH = CONTRACT / "example.json"
FORM_PATH = CONTRACT / "form.xlsx"

DOCUMENT_SHEET = "Документ"
INSTRUCTION_SHEET = "Инструкция"
DICTIONARY_SHEET = "Списки"
YES_NO = "Да / Нет"
HEADER_ROW = 1
FIRST_DATA_ROW = 2
STYLED_ROWS = 400
NESTED_PATH = 2
KEY_TITLE = "№ объекта"
PLACE_KEY = "external_id"
PLACE_REF = "__place__"

PLANS: tuple[dict[str, Any], ...] = (
    {
        "name": "Объекты",
        "def": "place",
        "skip": ("accessibility", "heritage", "opening_hours", "prices", "events"),
    },
    {
        "name": "Доступность и охрана",
        "def": "place",
        "groups": ("accessibility", "heritage"),
        "keyed": True,
        "merge": True,
    },
    {"name": "Режим работы", "def": "opening_hours", "keyed": True},
    {"name": "Цены", "def": "prices", "keyed": True},
    {"name": "События объекта", "def": "events", "keyed": True},
    {"name": "Инфраструктура", "def": "infrastructure", "root": "infrastructure"},
    {"name": "События площадок", "def": "venue_event", "root": "events"},
)

DOCUMENT_FIELDS = (
    ("version", "Версия формата", "Всегда 1"),
    ("sent_on", "Дата отправки", "ГГГГ-ММ-ДД"),
    ("sender", "Организация", "Кто подготовил данные"),
)

INSTRUCTION = (
    "Как заполнять эту форму",
    "",
    (
        "В одном файле — все объекты Гродненской области. Несколько строк уже заполнены "
        "как образец: их можно править, дописывать свои под ними и удалять ненужные."
    ),
    "",
    (
        "Неизвестное значение — пустая клетка. Не пишите прочерк, «нет данных» или «не "
        "знаю»: пустая клетка читается как «сведений нет», а прочерк — как значение."
    ),
    "",
    "Обязательное отмечено звёздочкой в заголовке. Остальное можно оставить пустым.",
    "",
    (
        "Числа — точкой как разделителем: 53.6808. Широта и долгота — в градусах, как в "
        "навигаторе, широта первая. Точка обязана попасть в Гродненскую область."
    ),
    "",
    "Даты — в виде 2026-11-01, время — в виде 10:00. По-русски «1 ноября» не писать.",
    "",
    "Ссылки — целиком, начиная с https://",
    "",
    (
        "Колонки с выпадающим списком берут значения из листа «Списки» — писать там своё "
        "нельзя, иначе строка к нам не попадёт."
    ),
    "",
    "Листы и как они связаны:",
    (
        "  Объекты — по строке на объект показа: замок, церковь, музей, парк. Первая "
        "колонка (№) — ваш учётный номер, по нему остальные листы находят объект."
    ),
    "  Доступность и охрана — по строке на объект, № тот же, что в «Объектах».",
    (
        "  Режим работы — сколько угодно строк на объект: одна запись «Все дни», если "
        "график одинаковый всю неделю, либо по строке на день или сезон."
    ),
    (
        "  Цены — сколько угодно строк на объект. В каждой строке либо сумма с валютой, "
        "либо отметка «бесплатно», либо примечание словами («экскурсия по договору»)."
    ),
    (
        "  События объекта — выставки, фестивали, экскурсии на объекте. № события нужен, "
        "чтобы при следующей присылке мы обновили событие, а не завели второе."
    ),
    (
        "  Инфраструктура — отдельно стоящие сооружения на маршруте: лестница, пандус, "
        "переход, подземный переход, лифт. Это не объекты показа, у них свой номер."
    ),
    (
        "  События площадок — события там, где объекта показа нет: площадь, ярмарка, "
        "городской праздник. Обязательны название и площадка; координаты нужны, чтобы "
        "событие встало на карту."
    ),
    "",
    (
        "Лист «Списки» не заполняется и не удаляется: из него собраны выпадающие списки. "
        "Лист «Документ» — версия формата, дата отправки и организация."
    ),
    "",
    (
        "Присылать можно этот файл. Данные мы принимаем в формате JSON, он описан в "
        "schema.json рядом; превратим форму в JSON сами и вернём список отказов, если "
        "что-то не сойдётся."
    ),
)


def _openpyxl() -> tuple[Any, Any, Any]:
    try:
        openpyxl = importlib.import_module("openpyxl")
        styles = importlib.import_module("openpyxl.styles")
        validation = importlib.import_module("openpyxl.worksheet.datavalidation")
    except ModuleNotFoundError:
        raise SystemExit(
            "openpyxl не установлен: uv run --with openpyxl python -m db.external_form"
        ) from None
    return openpyxl, styles, validation.DataValidation


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _definition(schema: dict[str, Any], name: str) -> dict[str, Any]:
    return schema["$defs"][name]


def _columns(schema: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    """The columns of one sheet, in the order the schema itself declares them."""
    definition = _definition(schema, plan["def"])
    required = set(definition.get("required", ()))
    properties = definition["properties"]
    columns: list[dict[str, Any]] = []
    if plan.get("keyed"):
        columns.append(
            {"title": KEY_TITLE, "path": (PLACE_REF,), "type": "string", "required": True}
        )
    groups = plan.get("groups", ())
    for name, spec in properties.items():
        if name in plan.get("skip", ()):
            continue
        if groups:
            if name not in groups:
                continue
            for nested, nested_spec in spec["properties"].items():
                columns.append(
                    {
                        "title": nested_spec.get("title", nested),
                        "path": (name, nested),
                        "type": nested_spec.get("type"),
                        "enum": nested_spec.get("enum"),
                        "required": False,
                    }
                )
            continue
        columns.append(
            {
                "title": spec.get("title", name),
                "path": (name,),
                "type": spec.get("type"),
                "enum": spec.get("enum"),
                "required": name in required,
            }
        )
    return columns


def _dictionary_for(schema: dict[str, Any], column: dict[str, Any]) -> str | None:
    if column.get("type") == "boolean":
        return YES_NO
    for name, values in schema["x-dictionaries"].items():
        if column.get("enum") == values:
            return name
    return None


def build(form_path: Path = FORM_PATH) -> int:
    openpyxl, styles, validation = _openpyxl()
    schema = _schema()
    document: dict[str, Any] = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    _write_instruction(workbook, styles)
    _write_document(workbook, styles, document)
    dictionaries = _write_dictionaries(workbook, styles, schema)
    for plan in PLANS:
        _write_sheet(workbook, (styles, validation), schema, document, plan, dictionaries)
    workbook.save(form_path)
    print(f"[form] {form_path} собран из схемы и примера")
    return 0


def _write_instruction(workbook: Any, styles: Any) -> None:
    sheet = workbook.create_sheet(INSTRUCTION_SHEET)
    sheet.column_dimensions["A"].width = 112
    for offset, line in enumerate(INSTRUCTION):
        cell = sheet.cell(row=1 + offset, column=1, value=line)
        if offset == 0:
            cell.font = styles.Font(bold=True, size=14)


def _write_document(workbook: Any, styles: Any, document: dict[str, Any]) -> None:
    sheet = workbook.create_sheet(DOCUMENT_SHEET)
    sheet.append(["Поле", "Значение", "Что это"])
    for name, title, hint in DOCUMENT_FIELDS:
        sheet.append([title, document.get(name), hint])
        sheet.cell(row=sheet.max_row, column=2).number_format = "@"
    _style_header(sheet, styles)
    sheet.column_dimensions["A"].width = 22
    sheet.column_dimensions["B"].width = 44
    sheet.column_dimensions["C"].width = 32


def _write_dictionaries(workbook: Any, styles: Any, schema: dict[str, Any]) -> dict[str, str]:
    """One column per dictionary; the drop-downs point here, not at a literal list."""
    sheet = workbook.create_sheet(DICTIONARY_SHEET)
    ranges: dict[str, str] = {}
    for index, (name, values) in enumerate(schema["x-dictionaries"].items(), start=1):
        header = sheet.cell(row=1, column=index, value=name)
        header.font = styles.Font(bold=True)
        for offset, value in enumerate(values, start=2):
            sheet.cell(row=offset, column=index, value=value)
        letter = header.column_letter
        ranges[name] = f"{DICTIONARY_SHEET}!${letter}$2:${letter}${len(values) + 1}"
    sheet.sheet_state = "hidden"
    return ranges


def _write_sheet(
    workbook: Any,
    tools: tuple[Any, Any],
    schema: dict[str, Any],
    document: dict[str, Any],
    plan: dict[str, Any],
    dictionaries: dict[str, str],
) -> None:
    styles, validation_class = tools
    sheet = workbook.create_sheet(plan["name"])
    columns = _columns(schema, plan)
    sheet.append([_header(column) for column in columns])
    _style_header(sheet, styles)
    for row in _document_rows(document, plan):
        sheet.append([_cell_value(column, row) for column in columns])
    for index, column in enumerate(columns, start=1):
        letter = sheet.cell(row=HEADER_ROW, column=index).column_letter
        _as_text(sheet, index)
        allowed = dictionaries.get(_dictionary_for(schema, column) or "")
        if allowed:
            validation = validation_class(type="list", formula1=allowed, allow_blank=True)
            sheet.add_data_validation(validation)
            validation.add(f"{letter}{FIRST_DATA_ROW}:{letter}{STYLED_ROWS}")
        width = max(len(column["title"]) + 3, 12)
        sheet.column_dimensions[letter].width = min(width, 38)
    sheet.freeze_panes = sheet.cell(row=FIRST_DATA_ROW, column=1)


def _header(column: dict[str, Any]) -> str:
    return column["title"] + (" *" if column["required"] else "")


def _as_text(sheet: Any, index: int) -> None:
    for row in range(FIRST_DATA_ROW, STYLED_ROWS + 1):
        sheet.cell(row=row, column=index).number_format = "@"


def _style_header(sheet: Any, styles: Any) -> None:
    for cell in sheet[HEADER_ROW]:
        cell.font = styles.Font(bold=True)
        cell.alignment = styles.Alignment(vertical="top")


def _document_rows(document: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    if plan.get("root"):
        return list(document.get(plan["root"], []))
    rows: list[dict[str, Any]] = []
    for place in document.get("places", []):
        if plan["def"] == "place":
            rows.append({**place, PLACE_REF: place[PLACE_KEY]})
            continue
        for entry in place.get(plan["def"], []):
            rows.append({**entry, PLACE_REF: place[PLACE_KEY]})
    return rows


def _cell_value(column: dict[str, Any], row: dict[str, Any]) -> Any:
    value: Any = row
    for step in column["path"]:
        if not isinstance(value, dict) or step not in value:
            return None
        value = value[step]
    if isinstance(value, bool):
        return "да" if value else "нет"
    return value


def read(form_path: Path) -> dict[str, Any]:
    openpyxl, _, _ = _openpyxl()
    schema = _schema()
    workbook = openpyxl.load_workbook(form_path, data_only=True)
    document = _read_document(workbook, schema)
    places = _read_place_rows(workbook, schema, PLANS[0])
    by_key = {str(place[PLACE_KEY]): place for place in places}
    for plan in PLANS[1:]:
        entries = _read_rows(workbook, schema, plan, nested=bool(plan.get("groups")))
        if plan.get("root"):
            document[plan["root"]] = entries
            continue
        for entry in entries:
            owner = by_key.get(str(entry.pop(PLACE_REF, "")))
            if owner is None:
                raise SystemExit(
                    f"[form] лист «{plan['name']}»: строка без объекта в листе «Объекты»"
                )
            if plan.get("merge"):
                owner.update(entry)
                continue
            owner.setdefault(plan["def"], []).append(entry)
    document["places"] = places
    return _in_schema_order(document)


def _read_place_rows(
    workbook: Any, schema: dict[str, Any], plan: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = _read_rows(workbook, schema, plan, nested=False)
    for row in rows:
        if not str(row.get(PLACE_KEY, "")).strip():
            raise SystemExit(f"[form] лист «{plan['name']}»: строка без № объекта")
    return rows


def _read_document(workbook: Any, schema: dict[str, Any]) -> dict[str, Any]:
    titles = {title: name for name, title, _ in DOCUMENT_FIELDS}
    document: dict[str, Any] = {}
    for row in workbook[DOCUMENT_SHEET].iter_rows(min_row=FIRST_DATA_ROW, values_only=True):
        title = str(row[0]).strip() if row and row[0] else ""
        name = titles.get(title)
        if name is None:
            continue
        value = row[1] if len(row) > 1 else None
        if value is None or str(value).strip() == "":
            raise SystemExit(f"[form] лист «{DOCUMENT_SHEET}»: пустое поле «{title}»")
        document[name] = _coerce(schema["properties"][name], value)
    for name, title, _ in DOCUMENT_FIELDS:
        if name not in document:
            raise SystemExit(f"[form] лист «{DOCUMENT_SHEET}»: нет строки «{title}»")
    return document


def _read_rows(
    workbook: Any, schema: dict[str, Any], plan: dict[str, Any], *, nested: bool
) -> list[dict[str, Any]]:
    sheet = workbook[plan["name"]]
    columns = _columns(schema, plan)
    index = {
        str(cell.value).strip(): position
        for position, cell in enumerate(sheet[HEADER_ROW])
        if cell.value is not None
    }
    rows: list[dict[str, Any]] = []
    for values in sheet.iter_rows(min_row=FIRST_DATA_ROW, values_only=True):
        if all(value is None or str(value).strip() == "" for value in values):
            continue
        record: dict[str, Any] = {}
        for column in columns:
            position = index.get(_header(column))
            if position is None or position >= len(values):
                continue
            raw = values[position]
            if raw is None or str(raw).strip() == "":
                continue
            value = _coerce(column, raw)
            if nested and len(column["path"]) == NESTED_PATH:
                record.setdefault(column["path"][0], {})[column["path"][1]] = value
            else:
                record[column["path"][0]] = value
        rows.append(record)
    return rows


def _coerce(column: dict[str, Any], value: Any) -> Any:
    if isinstance(value, dt.datetime | dt.date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, dt.time):
        return value.strftime("%H:%M")
    text = str(value).strip()
    if column.get("type") == "boolean":
        lowered = text.lower()
        if lowered not in ("да", "нет"):
            raise SystemExit(f"[form] «{column['title']}»: ожидается «да» или «нет», а не «{text}»")
        return lowered == "да"
    if column.get("type") == "integer":
        return int(float(text.replace(",", ".")))
    if column.get("type") == "number":
        number = float(text.replace(",", "."))
        return int(number) if number.is_integer() and "." not in text else number
    return text


def _in_schema_order(document: dict[str, Any]) -> dict[str, Any]:
    ordered: dict[str, Any] = {}
    for name in ("version", "sent_on", "sender", "places", "infrastructure", "events"):
        if name in document:
            ordered[name] = document[name]
    return ordered


def _validate(document: dict[str, Any]) -> None:
    try:
        validator_class = importlib.import_module("jsonschema.validators").Draft202012Validator
    except ModuleNotFoundError:
        raise SystemExit(
            "jsonschema не установлен: uv run --with jsonschema python -m db.external_form"
        ) from None
    validator = validator_class(_schema())
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    for error in errors[:20]:
        where = "/".join(str(part) for part in error.path) or "документ"
        print(f"[form] {where}: {error.message}", file=sys.stderr)
    if errors:
        raise SystemExit(f"[form] форма расходится со схемой в {len(errors)} местах")


def verify() -> int:
    """The form must carry the example unchanged: that is what a filled row is measured against."""
    example = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "form.xlsx"
        build(path)
        document = read(path)
    _validate(document)
    if document != example:
        for key in sorted(set(example) | set(document)):
            if example.get(key) != document.get(key):
                print(f"[form] {key} разошёлся с примером", file=sys.stderr)
        raise SystemExit("[form] форма и пример расходятся")
    print("[form] книга собирается из схемы и разбирается обратно в пример без потерь")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Сборка формы из схемы и разбор заполненной формы."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build", help="Собрать книгу из схемы и примера.")
    build_parser.add_argument("--out", type=Path, default=FORM_PATH)
    read_parser = commands.add_parser("read", help="Превратить заполненную книгу в документ.")
    read_parser.add_argument("form", type=Path)
    read_parser.add_argument("--out", type=Path, default=None)
    commands.add_parser(
        "verify", help="Собрать книгу во временный файл и разобрать обратно в пример."
    )
    args = parser.parse_args(argv)

    if args.command == "build":
        return build(args.out)
    if args.command == "verify":
        return verify()
    document = read(args.form)
    _validate(document)
    text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"[form] {args.out} записан")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
