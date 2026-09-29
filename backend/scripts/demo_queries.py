"""Прогнать пачку демонстрационных запросов и измерить, что из них годится.

Зачем: на демо нужны запросы, которые дают складный маршрут за разумное время,
а не те, где выходит две остановки или мусор в списке. Скрипт спрашивает агент
так же, как это делает панель (тот же POST /routes/generate), и пишет таблицу.

Запуск (из backend, с подхваченным .env):
    ./.venv/bin/python scripts/demo_queries.py
    ./.venv/bin/python scripts/demo_queries.py --base http://localhost --only 3,7
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# Координаты центра Гродно — как «моё местоположение» у туриста.
ORIGIN = {"lat": 53.6778, "lon": 23.8295}

# Подозрительные слова: попадание в название остановки значит, что в маршрут
# затесалось то, что туристу показывать неловко (найдено на прогонах раньше).
JUNK = ("казарм", "могил", "рядовая застройка", "братская")


@dataclass
class Case:
    """Один демонстрационный запрос."""

    label: str
    query: str
    minutes: int | None = None
    profile: str | None = None
    note: str = ""
    # Заполняется прогоном.
    status: int = 0
    plan_status: str = ""
    stops: list[str] = field(default_factory=list)
    length_km: float = 0.0
    time_seconds: float = 0.0
    elapsed: float = 0.0
    detail: str = ""
    junk: list[str] = field(default_factory=list)


CASES: list[Case] = [
    Case("старый город, 2 ч", "Старый город за два часа пешком", 120, "pedestrian"),
    Case("замки и костёлы", "Замки и костёлы Гродно", 180, "pedestrian"),
    Case("где поесть", "Где поесть в центре, недорого", 60, "pedestrian"),
    Case("вечерняя прогулка", "Вечерняя прогулка по Советской", 60, "pedestrian"),
    Case("с детьми", "С детьми: парки и замки", 180, "pedestrian"),
    Case("всё главное, без бюджета", "Все главные достопримечательности Гродно"),
    Case("две точки", "Фарный костёл и Новый замок", 120, "pedestrian"),
    Case("музеи", "Музеи Гродно", 120, "pedestrian"),
    Case("Коложская и замок", "Коложская церковь и Старый замок", 90, "pedestrian"),
    Case("кофейни", "Кофейни в центре", 60, "pedestrian"),
    Case("область, машина", "Замки Гродненской области", 480, "car"),
    Case("Ліда", "Что посмотреть в Лиде", 180, "pedestrian"),
    Case("вне зоны", "Мирский замок", 180, "car", note="ожидаем честный отказ"),
    Case("услуги рядом", "Кафе и туалеты по пути", 90, "pedestrian"),
]


def ask(base: str, case: Case, timeout: float = 300.0) -> None:
    """Один запрос. Всё, что нужно для решения, попадает в сам `case`."""
    body: dict[str, object] = {
        "query": case.query,
        "origin": ORIGIN,
        "language": "ru",
    }
    if case.minutes:
        body["time_budget_minutes"] = case.minutes
    if case.profile:
        body["profile"] = case.profile

    request = urllib.request.Request(
        f"{base}/routes/generate",
        data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            case.status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        case.status = error.code
        case.detail = error.read().decode("utf-8")[:200]
        case.elapsed = time.monotonic() - started
        return
    except Exception as error:
        case.detail = f"{type(error).__name__}: {error}"
        case.elapsed = time.monotonic() - started
        return

    case.elapsed = time.monotonic() - started
    case.plan_status = str(payload.get("status", ""))

    points = payload.get("points") or []
    case.stops = [str(point.get("name", "?")) for point in points]
    summary = payload.get("summary") or {}
    case.length_km = float(summary.get("length_km") or 0.0)
    case.time_seconds = float(summary.get("time_seconds") or 0.0)

    interpretation = payload.get("interpretation") or {}
    unmet = interpretation.get("unmet") or []
    if unmet:
        case.detail = f"не выполнено: {len(unmet)}"

    case.junk = [name for name in case.stops if any(word in name.lower() for word in JUNK)]


def verdict(case: Case) -> str:
    """Короткий вывод: годится ли запрос на демо."""
    if case.status != 200:
        return f"НЕ ГОДИТСЯ — HTTP {case.status} {case.detail}".strip()
    if case.plan_status == "infeasible":
        return "годится как негативный кейс (честный отказ)"
    if case.junk:
        return f"с оговоркой — мусор: {', '.join(case.junk)}"
    if len(case.stops) < 3:
        return f"слабо — всего {len(case.stops)} остановки"
    return "годится"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost")
    parser.add_argument("--only", default="", help="через запятую номера случаев")
    parser.add_argument("--json", default="", help="куда сохранить измерения")
    arguments = parser.parse_args()

    chosen = CASES
    if arguments.only:
        wanted = {int(part) for part in arguments.only.split(",") if part.strip()}
        chosen = [case for index, case in enumerate(CASES, start=1) if index in wanted]

    print(f"Замеряю {len(chosen)} запросов против {arguments.base}")
    for index, case in enumerate(chosen, start=1):
        ask(arguments.base, case)
        clock = f"{case.time_seconds / 60:.0f} мин" if case.time_seconds else "—"
        print(
            f"{index:>2}. {case.label:<24} {case.elapsed:>6.1f} с · "
            f"{case.plan_status:<10} · остановок {len(case.stops):<2} · "
            f"{case.length_km:>5.2f} км · {clock:<7} · {verdict(case)}"
        )
        if case.stops:
            print(f"     {', '.join(case.stops)}")

    if arguments.json:
        with open(arguments.json, "w", encoding="utf-8") as handle:
            json.dump([case.__dict__ for case in chosen], handle, ensure_ascii=False, indent=2)
        print(f"\nизмерения: {arguments.json}")


if __name__ == "__main__":
    main()
