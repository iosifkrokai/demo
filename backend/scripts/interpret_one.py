"""Разбор одного запроса одной моделью — без постройки маршрута.

Нужен, чтобы сравнить модели по тому, что они вообще извлекают из фразы
туриста: нынешняя модель на широкую просьбу возвращает одну обязательную точку
и НИ ОДНОГО интереса (`optional_categories: []`), и оптимизатору нечего
добавлять — маршрут выходит в две остановки.

Запуск (из backend, с подхваченным .env):
    AGENT_INTERPRET_MODEL=google/gemini-2.5-flash \
      ./.venv/bin/python scripts/interpret_one.py "Замки и костёлы Гродно"
"""

from __future__ import annotations

import json
import sys
import time

from agent.models import GenerateReq, LatLon
from agent.planner.agent_interpret import interpret_with_agent

ORIGIN = LatLon(lat=53.6778, lon=23.8295)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("нужен текст запроса")
    query = sys.argv[1]
    minutes = int(sys.argv[2]) if len(sys.argv) > 2 else None

    request = GenerateReq(query=query, origin=ORIGIN)
    if minutes:
        request.time_budget_minutes = minutes

    started = time.monotonic()
    try:
        result = interpret_with_agent(query, request)
    except Exception as error:
        print(json.dumps({"error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return
    elapsed = round(time.monotonic() - started, 1)

    if result is None:
        print(json.dumps({"error": "разбор не вернул требований", "seconds": elapsed}, ensure_ascii=False))
        return

    def kinds(items: object, kind: str) -> list[str]:
        found = []
        for item in items or []:
            if getattr(item, "kind", None) == kind:
                found.append(getattr(item, "name", None) or getattr(item, "code", None) or "?")
        return found

    requirements = list(getattr(result, "requirements", []) or [])
    print(
        json.dumps(
            {
                "seconds": elapsed,
                "must_visit": kinds(requirements, "must_visit"),
                "interests": kinds(requirements, "interest"),
                "service": kinds(requirements, "service"),
                "avoid": kinds(requirements, "avoid"),
                "всего_требований": len(requirements),
                "unknowns": list(getattr(result, "unknowns", []) or [])[:3],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
