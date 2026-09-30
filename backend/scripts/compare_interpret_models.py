"""Сравнение моделей разбора на живых запросах: что извлекается и за сколько секунд.

Главное отличие от «посчитать требования»: у каждого запроса есть ЗАРАНЕЕ
ИЗВЕСТНЫЙ правильный ответ, взятый не из вкуса, а из правил промпта и из
golden-набора. «Ноль требований» само по себе — не плохо: «Мир и Новогрудок
пешком» называет ТЕРРИТОРИИ, а территория по правилам промпта становится
областью поиска, а не остановкой, и ноль требований там — правильный ответ.
Мерить надо попадание в ожидания, а не объём вывода.

Ожидания по запросу:
  must_visit — подстрока в имени обязательного места;
  service    — код сервиса, который должен появиться;
  hard       — код, который обязан быть strength=hard;
  soft       — код, который обязан быть strength=soft (пере-настойчивость — дефект).

Запуск (нужны DATABASE_URL и OPENROUTER_API_KEY):
    ./.venv/bin/python scripts/compare_interpret_models.py
"""

from __future__ import annotations

import json
import os
import time

from agent.models import GenerateReq, LatLon

ORIGIN = LatLon(lat=53.6778, lon=23.8295)

# (запрос, бюджет минут, ожидания)
QUERIES: list[tuple[str, int | None, dict[str, list[str]]]] = [
    # «обязателен» — это hard; мягкое «по пути» — soft. Дешёвая ловушка на
    # пере-настойчивость: модель, ставящая hard всем сервисам, ломает маршрут.
    ("Старый город за два часа, туалет обязателен", 120, {"hard": ["туалет"]}),
    # Именованное место ДОЛЖНО стать обязательной остановкой, а мягкое кафе —
    # мягким: здесь ноль требований был бы настоящей ошибкой.
    (
        "Мирский замок обязательно, кофе по пути, два часа",
        120,
        {"must_visit": ["Мир"], "service": ["кафе"], "soft": ["кафе"]},
    ),
    # Английский язык и сервис «по пути» — soft, не hard.
    (
        "Old town in two hours, a toilet on the way",
        120,
        {"service": ["туалет"], "soft": ["туалет"]},
    ),
]

# Порядок — по цене: дорогая строка сверху, потому что у ключа жёсткий потолок и
# дорогую модель надо успеть измерить, а дешёвые измеришь и позже.
MODELS: list[tuple[str, str]] = [
    ("google/gemini-2.5-pro", "$1.25/1M — incumbent, точка отсчёта"),
    ("deepseek/deepseek-v4.1-flash", "$0.30/1M — 2026-09-10"),
    ("cohere/command-a-plus", "$0.30/1M — 2026-09-22"),
    ("google/gemini-2.5-flash", "$0.30/1M — победитель первого раунда"),
    ("z-ai/glm-5.3-flash", "$0.15/1M — 2026-08-26"),
    ("qwen/qwen3.8-omni-flash", "$0.15/1M — 2026-09-21"),
    ("xiaomi/mimo-v2.6-flash", "$0.14/1M — 2026-09-21"),
]


def _score(result: object, expectations: dict[str, list[str]]) -> tuple[int, list[str]]:
    """Сколько ожиданий выполнил разбор и какие именно пропустил."""
    reqs = getattr(result, "requirements", None) or []
    missed: list[str] = []

    for needle in expectations.get("must_visit", []):
        hit = any(
            r.kind == "must_visit" and r.name and needle.lower() in r.name.lower()
            for r in reqs
        )
        if not hit:
            missed.append(f"must_visit~{needle}")

    for code in expectations.get("service", []):
        if not any(r.kind == "service" and r.code == code for r in reqs):
            missed.append(f"service:{code}")

    for code in expectations.get("hard", []):
        if not any(r.code == code and r.strength == "hard" for r in reqs):
            missed.append(f"hard:{code}")

    for code in expectations.get("soft", []):
        if not any(r.code == code and r.strength == "soft" for r in reqs):
            missed.append(f"soft:{code}")

    asked = sum(len(v) for v in expectations.values())
    return asked - len(missed), missed


def main() -> None:
    met_by_model: dict[str, list[int]] = {}

    for model, note in MODELS:
        os.environ["AGENT_INTERPRET_MODEL"] = model
        # модель читается при импорте/вызове — перезагрузим модуль, чтобы взять новую
        import importlib

        from agent.planner import agent_interpret

        importlib.reload(agent_interpret)
        interpret = agent_interpret.interpret_with_agent

        print(f"\n=== {model} — {note} ===")
        met_all = 0
        asked_all = 0

        for query, minutes, expectations in QUERIES:
            asked = sum(len(v) for v in expectations.values())
            asked_all += asked
            request = GenerateReq(query=query, origin=ORIGIN)
            if minutes:
                request.time_budget_minutes = minutes
            started = time.monotonic()
            try:
                result = interpret(query, request)
            except Exception as error:
                print(f"  [{query[:38]:38}] ОШИБКА: {type(error).__name__}: {error}")
                continue
            elapsed = round(time.monotonic() - started, 1)
            if result is None:
                print(
                    f"  [{query[:38]:38}] {elapsed:5.1f}с  разбор не вернулся "
                    "(фоллбек на ключевые слова)"
                )
                continue

            met, missed = _score(result, expectations)
            met_all += met
            reqs = result.requirements or []
            shown = [f"{r.kind}:{r.code or r.name}({r.strength})" for r in reqs]
            print(
                f"  [{query[:38]:38}] {elapsed:5.1f}с  ожидания {met}/{asked}"
                f"  районы: {json.dumps(result.areas, ensure_ascii=False)}"
                f"  {json.dumps(shown, ensure_ascii=False)}"
            )
            if missed:
                print(f"       ПРОПУЩЕНО: {', '.join(missed)}")

        met_by_model[model] = [met_all, asked_all]

    print("\n" + "=" * 74)
    print("ИТОГ: выполнено ожиданий / предъявлено")
    print("=" * 74)
    for model, (met, asked) in sorted(met_by_model.items(), key=lambda kv: -kv[1][0]):
        print(f"  {f'{met}/{asked}' if asked else '—':>8}  {model}")


if __name__ == "__main__":
    main()
