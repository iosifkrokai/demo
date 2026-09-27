"""Один отчёт о качестве: что мы знаем, чего не знаем и где растём.

Три слоя мерят разное, и ни один не заменяет другой:

  * ``benchmarks/routes``  — геометрия: близко ли маршрут к эталонной прогулке
                             (recall@K, порядок τ, крюк, длина ног, бюджет);
  * ``benchmarks/golden``  — выдержал ли запрос конвейер (условия: категории,
                             запреты, регион, бюджет, паритет RU/EN);
  * ``evals``              — какой участок флоу виноват, когда ответ плох.

Этот отчёт **ничего не измеряет сам**. Он читает то, что слои уже записали, и
показывает одним экраном числа, свежесть каждого и точки роста. Числа слоёв не
складываются ни в какой «общий балл»: у них нет общей шкалы, и сумма была бы
выдумкой — а выдумка здесь дороже отсутствия числа.

Запуск:

    ./.venv/bin/python reports/quality.py                 # прочитать готовое
    ./.venv/bin/python reports/quality.py --run            # снять офлайн-слои заново
    ./.venv/bin/python reports/quality.py --run --golden   # плюс живой golden (нужен стек)

Пустой слой печатается как «не измерялось» вместе с командой, которая его снимет,
и никогда — как зелёный.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parent.parent
SNAPSHOTS = BACKEND / "benchmarks" / "snapshots"
EVALS_LAST = BACKEND / "evals" / "last.json"
VENV = BACKEND / ".venv" / "bin" / "python"

#: Свежее какого срока считается несвежим. Не «плохо» — просто повод посмотреть
#: на дату, прежде чем верить числу.
STALE_DAYS = 14


def _mtime(path: Path) -> str:
    if not path.exists():
        return "нет"
    stamp = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    return stamp.strftime("%d.%m %H:%M")


def _age_days(path: Path) -> float | None:
    if not path.exists():
        return None
    delta = datetime.now(UTC) - datetime.fromtimestamp(path.stat().st_mtime, UTC)
    return delta.total_seconds() / 86400


def _latest_under(root: Path, name: str) -> Path | None:
    """Самый свежий файл `name` в подпапках (снимки складываются по датам)."""
    if not root.exists():
        return None
    found = [p / name for p in root.iterdir() if (p / name).exists()]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


# ── слой: геометрия маршрутов (benchmarks/routes) ────────────────────────────

def read_routes(path: Path | None = None) -> dict[str, Any]:
    path = path or _latest_under(SNAPSHOTS, "report.json")
    if path is None or not path.exists():
        return {
            "layer": "benchmarks/routes",
            "measured": False,
            "how": "./.venv/bin/python scripts/bench_routes.py --replay "
                   "benchmarks/snapshots/<снимок> --report-dir benchmarks/snapshots/<дата>",
        }
    report = json.loads(path.read_text(encoding="utf-8"))
    overall = report.get("overall") or {}
    want = [
        m for m in ("recall_at_k", "kendall_tau", "detour_km", "stage.recall")
        if m in overall
    ]
    return {
        "layer": "benchmarks/routes",
        "measured": True,
        "source": str(path.relative_to(BACKEND)),
        "taken": _mtime(path),
        "age_days": _age_days(path),
        "n_runs": report.get("n_runs"),
        "metrics": {
            m: {
                "label": overall[m].get("label") or m,
                "mean": overall[m].get("mean"),
                "lo": overall[m].get("lo"),
                "hi": overall[m].get("hi"),
                "n": overall[m].get("n"),
            }
            for m in want
        },
        "hard_failures": report.get("hard_failures") or [],
        "notes": report.get("metric_notes") or [],
        "noise_floor": report.get("noise_floor"),
    }


# ── слой: соответствие запросу (benchmarks/golden) ───────────────────────────

def read_golden(path: Path | None = None) -> dict[str, Any]:
    path = path or _latest_under(SNAPSHOTS, "compliance.json")
    if path is None or not path.exists():
        return {
            "layer": "benchmarks/golden",
            "measured": False,
            "how": "./.venv/bin/python scripts/bench_routes.py --golden --snapshot \"\" "
                   "(нужен живой стек: приложение + Valhalla + ключ модели)",
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    summary = data.get("summary") or {}
    cases = data.get("cases") or []
    failed: list[dict[str, Any]] = []
    for case in cases:
        checks = case.get("checks") or case.get("conditions") or []
        bad = [
            c for c in checks
            if isinstance(c, dict) and not (c.get("ok") is True or c.get("passed") is True)
        ]
        if bad or case.get("ok") is False or case.get("passed") is False:
            failed.append(
                {
                    "case": case.get("id") or case.get("case"),
                    "why": case.get("query"),
                    "detail": "; ".join(
                        str(c.get("name") or c.get("check")) + ": " + str(c.get("detail"))
                        for c in bad
                    ) or "кейс не выдержал условий",
                }
            )
    return {
        "layer": "benchmarks/golden",
        "measured": True,
        "source": str(path.relative_to(BACKEND)),
        "taken": _mtime(path),
        "age_days": _age_days(path),
        "compliance": summary.get("compliance") or summary.get("rate"),
        "passed": summary.get("passed"),
        "total": summary.get("total") or summary.get("n_cases") or len(cases),
        "parity": summary.get("parity"),
        "failed_cases": failed,
        "mode": data.get("mode"),
    }


# ── слой: участки флоу (evals) ───────────────────────────────────────────────

def read_evals(path: Path | None = None) -> dict[str, Any]:
    path = path or EVALS_LAST
    if not path.exists():
        return {
            "layer": "evals",
            "measured": False,
            "how": "./.venv/bin/python evals/run.py --with-interpretation --json evals/last.json",
        }
    stages = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    for stage in stages:
        counted = [c for c in stage["checks"] if not c.get("known_gap")]
        passed = sum(1 for c in counted if c["ok"])
        failures: dict[str, int] = {}
        for c in stage["checks"]:
            if not c["ok"] and not c.get("known_gap"):
                failures[c["check"]] = failures.get(c["check"], 0) + 1
        out.append(
            {
                "stage": stage["stage"],
                "passed": passed,
                "total": len(counted),
                "skipped": stage.get("skipped"),
                "failures": failures,
                "gaps": [
                    {"case": c["case"], "why": c["why"]}
                    for c in stage["checks"] if c.get("known_gap")
                ],
            }
        )
    return {
        "layer": "evals",
        "measured": True,
        "source": str(path.relative_to(BACKEND)),
        "taken": _mtime(path),
        "age_days": _age_days(path),
        "stages": out,
    }


# ── точки роста ─────────────────────────────────────────────────────────────

def growth_points(routes: dict, golden: dict, evals: dict) -> list[dict[str, Any]]:
    """Всё известное плохое, отсортированное по числу затронутых кейсов.

    Сортировка именно такая и по этой причине: важность здесь судить нечем —
    нет ни одного числа о том, сколько людей спотыкается о каждую точку. Число
    затронутых кейсов честно измеримо, поэтому оно и стоит первым. Там, где
    затронутость неизвестна, стоит «?» — и точка всё равно видна.
    """
    points: list[dict[str, Any]] = []

    hard = routes.get("hard_failures") or {}
    for kind, count in (hard.get("by_kind") or {}).items():
        details = [
            d.get("detail", "") for d in (hard.get("details") or [])
            if d.get("kind") == kind
        ]
        points.append(
            {
                "what": f"геометрия: {kind}",
                "detail": "; ".join(details) or f"прогонов затронуто: {count}",
                "affects": count,
                "where": routes.get("source", "benchmarks/routes"),
            }
        )
    if hard.get("leg_sanity_defects"):
        points.append(
            {
                "what": "геометрия: дефекты самих ног оставлены в средних",
                "detail": f"дефектных ног: {hard['leg_sanity_defects']} — выкинуть прогон "
                          f"значило бы спрятать проверку",
                "affects": hard["leg_sanity_defects"],
                "where": routes.get("source", "benchmarks/routes"),
            }
        )

    for case in golden.get("failed_cases") or []:
        points.append(
            {
                "what": f"запрос не выдержал условий: {case['case']}",
                "detail": case.get("detail") or "",
                "affects": 1,
                "where": golden.get("source", "benchmarks/golden"),
            }
        )

    for stage in evals.get("stages") or []:
        for check, count in (stage.get("failures") or {}).items():
            points.append(
                {
                    "what": f"участок {stage['stage']}: {check}",
                    "detail": f"кейсов затронуто: {count}"
                              + (f" (участок {stage['skipped']})" if stage.get("skipped") else ""),
                    "affects": count,
                    "where": evals.get("source", "evals"),
                }
            )
        if stage.get("skipped"):
            points.append(
                {
                    "what": f"участок {stage['stage']} не измерялся",
                    "detail": str(stage["skipped"]),
                    "affects": 0,
                    "where": evals.get("source", "evals"),
                }
            )

    return sorted(points, key=lambda p: -p["affects"])


def known_gaps(routes: dict, golden: dict, evals: dict) -> list[dict[str, Any]]:
    """Документированные пробелы: видны, но провалом не считаются."""
    gaps: list[dict[str, Any]] = []
    for stage in evals.get("stages") or []:
        for gap in stage.get("gaps") or []:
            gaps.append({"where": f"evals/{stage['stage']}", **gap})
    for note in (golden.get("failed_cases") or []):
        if note.get("known_gap"):
            gaps.append({"where": "benchmarks/golden", "case": note["case"], "why": note.get("why")})
    return gaps


# ── вывод ────────────────────────────────────────────────────────────────────

NOT_MEASURED = [
    "вкус маршрута глазами человека — ни один слой не смотрит на прогулку как турист",
    "мобильная карточка точки и жесты на телефоне",
    "сервис перевода: подписи на карте — растровые тайлы, они не переводятся",
    "регионы вне Гродненской области — только негативные кейсы",
    "детур через Valhalla: «+N минут» станет числом лишь тогда, когда его построит маршрутизатор",
]


def render(routes: dict, golden: dict, evals: dict) -> str:
    out: list[str] = []
    out.append("")
    out.append("КАЧЕСТВО ГИДА — ОДНА СТРАНИЦА")
    out.append("=" * 66)
    out.append("")

    # ── числа по слоям ──
    out.append("СЛОЙ                  ЧИСЛО                                  СНЯТО")
    out.append("-" * 66)

    if routes.get("measured"):
        rec = routes["metrics"].get("recall_at_k") or {}
        number = (
            f"Rec@K {rec.get('mean'):.3f} [{rec.get('lo'):.3f}, {rec.get('hi'):.3f}]"
            if rec.get("mean") is not None else "метрик нет"
        )
        out.append(f"{'benchmarks/routes':<21} {number:<38} {routes['taken']}")
        out.append(
            f"{'':<21} {'(геометрия против эталонной прогулки)':<38} n={routes.get('n_runs')}"
        )
    else:
        out.append(f"{'benchmarks/routes':<21} {'НЕ ИЗМЕРЯЛОСЬ':<38} —")

    if golden.get("measured"):
        rate = golden.get("compliance")
        number = (
            f"соответствие {rate:.0%} ({golden.get('passed')}/{golden.get('total')})"
            if isinstance(rate, float) else
            f"выдержали {golden.get('passed')}/{golden.get('total')}"
        )
        out.append(f"{'benchmarks/golden':<21} {number:<38} {golden['taken']}")
        out.append(f"{'':<21} {'(выдержал ли запрос конвейер)':<38} {golden.get('mode') or ''}")
    else:
        out.append(f"{'benchmarks/golden':<21} {'НЕ ИЗМЕРЯЛОСЬ':<38} —")

    if evals.get("measured"):
        passed = sum(s["passed"] for s in evals["stages"])
        total = sum(s["total"] for s in evals["stages"])
        out.append(
            f"{'evals':<21} "
            f"{f'участки флоу {passed}/{total}' if total else 'участки не считались':<38} "
            f"{evals['taken']}"
        )
        for stage in evals["stages"]:
            note = "пропущен" if stage.get("skipped") else f"{stage['passed']}/{stage['total']}"
            line = "  " + stage["stage"] + ": " + note
            out.append(f"{'':<21} {line:<38}")
    else:
        out.append(f"{'evals':<21} {'НЕ ИЗМЕРЯЛОСЬ':<38} —")

    # ── точки роста ──
    points = growth_points(routes, golden, evals)
    out.append("")
    if points:
        out.append(f"ТОЧКИ РОСТА ({len(points)}) — по числу затронутых кейсов")
        out.append("-" * 66)
        out.append("важность здесь судить нечем: людей на точку мы не считаем,")
        out.append("поэтому первым стоит то, что задевает больше кейсов, а не то, что важнее.")
        out.append("")
        for point in points:
            affects = point["affects"] if point["affects"] else "?"
            out.append(f"  [{affects}] {point['what']}")
            if point["detail"]:
                out.append(f"        {point['detail']}")
            out.append(f"        источник: {point['where']}")
    else:
        out.append("ТОЧЕК РОСТА НЕТ")
        out.append("-" * 66)
        out.append("Проваленных проверок ни в одном слое. Это значит «поведение совпало")
        out.append("с записанным контрактом», а не «продукт хорош»: кейсы написаны нами")
        out.append("и узкие, а вкус прогулки не меряет ни один слой.")

    # ── пробелы ──
    gaps = known_gaps(routes, golden, evals)
    if gaps:
        out.append("")
        out.append(f"ИЗВЕСТНЫЕ ПРОБЕЛЫ ({len(gaps)}) — видны, но не считаются провалом")
        out.append("-" * 66)
        for gap in gaps:
            out.append(f"  {gap.get('where')}: {gap['case']}")

    # ── предупреждения о числах ──
    caveats: list[str] = []
    notes = routes.get("notes") or {}
    if isinstance(notes, dict):
        for metric, note in notes.items():
            caveats.append(f"{metric}: {note}")
    else:
        caveats.extend(str(note) for note in notes)
    for routes_metric, metric in (routes.get("metrics") or {}).items():
        if routes_metric == "stage.recall" and metric.get("mean") == 1.0:
            caveats.append(
                "stage-2 recall читается 1.000 по построению: пул — это остановки самого "
                "маршрута, он не может промахнуться. Числом это станет, когда API отдаст "
                "пул кандидатов шире маршрута."
            )
    for layer, name in ((routes, "routes"), (golden, "golden"), (evals, "evals")):
        age = layer.get("age_days")
        if age is not None and age > STALE_DAYS:
            caveats.append(
                f"{name}: числам {age:.0f} дн. — снимите заново, прежде чем верить "
                f"({layer.get('how') or layer.get('source')})"
            )
    if routes.get("noise_floor"):
        caveats.append(f"шумовой порог (не из этого прогона): {routes['noise_floor']}")
    if caveats:
        out.append("")
        out.append("ЧЕМ ЭТИ ЧИСЛА НЕ ЯВЛЯЮТСЯ")
        out.append("-" * 66)
        for caveat in dict.fromkeys(caveats):
            out.append(f"  • {caveat}")

    # ── чего нет ──
    out.append("")
    out.append("ЧЕГО ЗДЕСЬ НЕТ ВООБЩЕ")
    out.append("-" * 66)
    for item in NOT_MEASURED:
        out.append(f"  • {item}")

    # ── как снять заново ──
    out.append("")
    out.append("КАК СНЯТЬ ЗАНОВО")
    out.append("-" * 66)
    for layer, name in ((routes, "routes"), (golden, "golden"), (evals, "evals")):
        if not layer.get("measured"):
            out.append(f"  {name}: {layer['how']}")
    out.append("  всё офлайн сразу: ./.venv/bin/python reports/quality.py --run")
    out.append("")
    return "\n".join(out)


def _run_offline(days: int = 3) -> None:
    """Снять офлайн-слои заново: геометрия (replay) и участки флоу."""
    snapshot = _latest_under(SNAPSHOTS, "rows.jsonl")
    if snapshot is not None:
        out = BACKEND / "benchmarks" / "snapshots" / datetime.now(UTC).strftime("%Y-%m-%d")
        out.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [str(VENV), "scripts/bench_routes.py", "--replay", str(snapshot.parent),
             "--report-dir", str(out)],
            cwd=BACKEND, check=False,
        )
    subprocess.run(
        [str(VENV), "evals/run.py", "--json", "evals/last.json"],
        cwd=BACKEND, check=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Одна страница о качестве гида")
    parser.add_argument("--run", action="store_true", help="снять офлайн-слои заново")
    parser.add_argument(
        "--golden", action="store_true",
        help="снять и живой golden (нужен стек и ключ модели; платно)",
    )
    parser.add_argument("--json", type=Path, default=None, help="записать машинный отчёт")
    args = parser.parse_args(argv)

    if args.run:
        _run_offline()
    if args.golden:
        out_dir = SNAPSHOTS / datetime.now(UTC).strftime("%Y-%m-%d")
        out_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [str(VENV), "scripts/bench_routes.py", "--golden", "--snapshot", "",
             "--report-dir", str(out_dir)],
            cwd=BACKEND, check=False,
        )

    routes, golden, evals = read_routes(), read_golden(), read_evals()
    print(render(routes, golden, evals))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "routes": routes, "golden": golden, "evals": evals,
                    "growth_points": growth_points(routes, golden, evals),
                    "known_gaps": known_gaps(routes, golden, evals),
                    "not_measured": NOT_MEASURED,
                },
                ensure_ascii=False, indent=1,
            ),
            encoding="utf-8",
        )
        print(f"машинный отчёт: {args.json}")

    # Отчёт — чтение, а не гейт: он не падает от чужих провалов. Он падает от
    # собственной неспособности показать числа — иначе «пусто» выглядело бы как
    # «хорошо».
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
