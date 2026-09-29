"""Сравнение моделей разбора на живых запросах: что извлекается и за сколько секунд."""

from __future__ import annotations

import json
import os
import time

from agent.models import GenerateReq, LatLon

ORIGIN = LatLon(lat=53.6778, lon=23.8295)

QUERIES = [
    ("Старый город за два часа пешком", 120),
    ("Замки и костёлы Гродно", None),
    ("Хочу всё интересное, есть 3 часа, с детьми", 180),
    ("Мир и Новогрудок пешком", None),
]

MODELS = [
    "google/gemini-2.5-pro",          # текущий — точка отсчёта
    "stealth/space-bunny-alpha",      # бесплатный
    "openai/gpt-6-luna",              # $0.10/1M, 2026
    "xiaomi/mimo-v2.6-flash",         # $0.14/1M, 2026
    "anthropic/claude-sonnet-5.5",    # $2.00/1M, флагман, самый свежий
]

for model in MODELS:
    os.environ["AGENT_INTERPRET_MODEL"] = model
    # модель читается при импорте/вызове — перезагрузим модуль, чтобы взять новую
    import importlib

    from agent.planner import agent_interpret

    importlib.reload(agent_interpret)
    interpret = agent_interpret.interpret_with_agent

    print(f"\n=== {model} ===")
    for query, minutes in QUERIES:
        request = GenerateReq(query=query, origin=ORIGIN)
        if minutes:
            request.time_budget_minutes = minutes
        started = time.monotonic()
        try:
            result = interpret(query, request)
        except Exception as error:
            print(f"  [{query[:34]:34}] ОШИБКА: {type(error).__name__}: {error}")
            continue
        elapsed = round(time.monotonic() - started, 1)
        if result is None:
            print(f"  [{query[:34]:34}] {elapsed:5.1f}с  →  РАЗБОР НЕ ВЕРНУЛСЯ (фоллбек на ключевые слова)")
            continue
        reqs = result.requirements or []
        must = [r for r in reqs if r.strength in ("must", "hard")]
        opt = [r for r in reqs if r.strength not in ("must", "hard")]
        def one(r):
            return f"{r.kind}:{r.code or r.name or '—'}({r.strength})"
        print(
            f"  [{query[:34]:34}] {elapsed:5.1f}с  всего: {len(reqs)}  "
            f"районы: {json.dumps(result.areas, ensure_ascii=False)}  "
            f"непонятое: {len(result.unknowns)}  "
            f"{json.dumps([one(r) for r in reqs], ensure_ascii=False)}"
        )
