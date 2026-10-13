#!/usr/bin/env python
"""Trajectory evals: which tools the agent called, and with what arguments."""

from __future__ import annotations

import argparse
import functools
import inspect
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from pydantic_ai.messages import ModelResponse, ToolCallPart  # noqa: E402
from pydantic_ai.models.function import AgentInfo, FunctionModel  # noqa: E402

from agent import llm  # noqa: E402
from agent.client import plan_with_agent  # noqa: E402
from agent.models import ReaderBrief  # noqa: E402
from agent.tools import (  # noqa: E402
    areas as areas_mod,
    places as places_mod,
    plan as plan_mod,
    services as services_mod,
    verify as verify_mod,
)

QUALITY = BACKEND / "quality"
CASES = QUALITY / "cases" / "trajectory.jsonl"
REPORTS = QUALITY / "reports"
DEFAULT_OUT = REPORTS / "trajectory.json"

FAKE_KEY = "trajectory-offline"

TOOL_TARGETS: dict[str, tuple[Any, str]] = {
    "find_areas": (areas_mod, "find_areas"),
    "find_places": (places_mod, "find_places"),
    "plan_walk": (plan_mod, "plan_walk"),
    "get_place_facts": (places_mod, "get_place_facts"),
    "check_requirements": (verify_mod, "check_requirements"),
    "nearby_services": (services_mod, "nearby_services"),
}
PLUMBING_ARGS = frozenset({"repos", "facts", "deadline_s"})

BRIEF_KEYS = frozenset(
    {
        "locale",
        "profile",
        "time_budget_minutes",
        "party_children",
        "origin",
        "hard_services",
        "interests",
        "avoid",
        "result_mode",
        "round_trip",
    }
)
TUPLE_BRIEF_KEYS = ("hard_services", "interests", "avoid")


@dataclass
class ToolStep:
    """One tool call the loop made, with its arguments and the answer it gave."""

    name: str
    args: dict[str, Any]
    result: Any = None
    error: str | None = None


@dataclass
class Trajectory:
    """Everything one scripted run did: the calls, and the code's own verdict."""

    steps: list[ToolStep] = field(default_factory=list)
    submitted: bool = False
    status: str | None = None

    @property
    def tool_names(self) -> list[str]:
        return [s.name for s in self.steps]

    @property
    def n_tool_calls(self) -> int:
        """Work calls, excluding the terminal submission."""
        return sum(1 for s in self.steps if s.name != "submit_plan")


def _matches(actual: Any, wanted: Any) -> bool:
    """Is every key/value in ``wanted`` present in ``actual``? (a subset check)."""
    if isinstance(wanted, dict):
        if not isinstance(actual, dict):
            return False
        return all(k in actual and _matches(actual[k], v) for k, v in wanted.items())
    return actual == wanted


def score_case(case: dict[str, Any], trajectory: Trajectory) -> dict[str, Any]:
    """Every expectation of one case against one recorded trajectory, by code."""
    expect = case["expect"]
    checks: dict[str, dict[str, Any]] = {}

    wanted_tools = [str(t) for t in expect["tools"]]
    got_tools = trajectory.tool_names
    checks["tools"] = {
        "ok": got_tools == wanted_tools,
        "detail": f"вызвано {got_tools}, ожидалось {wanted_tools}",
    }

    if "max_tool_calls" in expect:
        cap = int(expect["max_tool_calls"])
        checks["max_tool_calls"] = {
            "ok": trajectory.n_tool_calls <= cap,
            "detail": f"{trajectory.n_tool_calls} рабочих вызовов при пределе {cap}",
        }

    for tool, wanted_args in (expect.get("args") or {}).items():
        found = any(s.name == tool and _matches(s.args, wanted_args) for s in trajectory.steps)
        checks[f"args:{tool}"] = {
            "ok": found,
            "detail": f"{tool} с аргументами {wanted_args} — {'есть' if found else 'не найден'}",
        }

    for tool, wanted_result in (expect.get("results") or {}).items():
        step = next((s for s in trajectory.steps if s.name == tool), None)
        ok = step is not None and _matches(step.result, wanted_result)
        checks[f"result:{tool}"] = {
            "ok": ok,
            "detail": f"ответ {tool}: {getattr(step, 'result', None)!r}",
        }

    for tool, wanted_error in (expect.get("errors") or {}).items():
        step = next((s for s in trajectory.steps if s.name == tool), None)
        ok = step is not None and step.error == wanted_error
        checks[f"error:{tool}"] = {
            "ok": ok,
            "detail": f"ошибка {tool}: {getattr(step, 'error', None)!r}, "
            f"ожидалась {wanted_error!r}",
        }

    if "submitted" in expect:
        wanted = bool(expect["submitted"])
        checks["submitted"] = {
            "ok": trajectory.submitted == wanted,
            "detail": f"план сдан={trajectory.submitted}, ожидалось {wanted}",
        }

    if "status" in expect:
        allowed = [str(s) for s in expect["status"]]
        checks["status"] = {
            "ok": trajectory.status in allowed,
            "detail": f"статус {trajectory.status!r}, ожидался один из {allowed}",
        }

    failed = [name for name, check in checks.items() if not check["ok"]]
    return {"checks": checks, "passed": not failed, "failed": failed}


def _resolve(value: Any, ids: list[int]) -> Any:
    """Replace the ``@stops`` / ``@stop`` placeholders with discovered ids."""
    if value == "@stops":
        return list(ids)
    if value == "@stop":
        return ids[0] if ids else None
    if isinstance(value, list):
        return [_resolve(v, ids) for v in value]
    if isinstance(value, dict):
        return {k: _resolve(v, ids) for k, v in value.items()}
    return value


def _brief(case: dict[str, Any]) -> ReaderBrief:
    raw = case.get("brief") or {}
    kwargs: dict[str, Any] = {}
    for key in BRIEF_KEYS:
        if key not in raw:
            continue
        value = raw[key]
        if key == "origin" and value is not None:
            value = (float(value[0]), float(value[1]))
        elif key in TUPLE_BRIEF_KEYS:
            value = tuple(value)
        kwargs[key] = value
    return ReaderBrief(**kwargs)


def _script_steps(case: dict[str, Any], ids: list[int]) -> list[ModelResponse]:
    steps = [
        ModelResponse(
            [ToolCallPart(call["tool"], json.dumps(_resolve(call.get("args") or {}, ids)))]
        )
        for call in case["script"]
    ]
    if case.get("finish") is not None:
        payload = json.dumps(_resolve(case["finish"], ids))
        steps.append(ModelResponse([ToolCallPart("submit_plan", payload)]))
    return steps


def _record_args(fn: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """The named arguments of a tool call, plumbing (repos/facts) left out."""
    try:
        bound = inspect.signature(fn).bind(*args, **kwargs)
    except TypeError:
        return {"_positional": list(args)} if args else {}
    return {k: v for k, v in bound.arguments.items() if k not in PLUMBING_ARGS}


def _make_wrapper(record: list[ToolStep], name: str, original: Any) -> Any:
    """One recorder bound to a single tool function (no late-binding surprises)."""

    @functools.wraps(original)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        error = result.get("error") if isinstance(result, dict) else None
        record.append(
            ToolStep(
                name=name,
                args=_record_args(original, args, kwargs),
                result=result,
                error=str(error) if error else None,
            )
        )
        return result

    return wrapper


def _recording(record: list[ToolStep]) -> list[tuple[Any, str, Any]]:
    """Wrap each registered tool's function so the call is recorded as it runs."""
    patches: list[tuple[Any, str, Any]] = []
    for name, (module, attr) in TOOL_TARGETS.items():
        original = getattr(module, attr)
        setattr(module, attr, _make_wrapper(record, name, original))
        patches.append((module, attr, original))
    return patches


def _restore(patches: list[tuple[Any, str, Any]]) -> None:
    for module, attr, original in patches:
        setattr(module, attr, original)


def run_case(case: dict[str, Any], repos: Any) -> Trajectory:
    """Play one case's script through the real run and record what the code did."""
    ids: list[int] = []
    discover = case.get("discover")
    if discover:
        near = discover["near"]
        ids = [
            row.id
            for row in repos.places.nearby(
                float(near[0]),
                float(near[1]),
                radius_km=float(discover["radius_km"]),
                limit=int(discover["limit"]),
            )
            if row.id is not None
        ]

    steps = _script_steps(case, ids)

    def _next(_messages: list[Any], _info: AgentInfo) -> ModelResponse:
        if not steps:
            raise AssertionError(f"{case['id']}: the script ran out of turns")
        return steps.pop(0)

    calls: list[ToolStep] = []
    patches = _recording(calls)
    try:
        with (
            mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": FAKE_KEY}),
            mock.patch.object(
                llm, "make_model", lambda: FunctionModel(_next, model_name="fake-agent")
            ),
        ):
            outcome = plan_with_agent(case["query"], _brief(case), repos=repos)
    finally:
        _restore(patches)

    trajectory = Trajectory(steps=calls, submitted=outcome is not None)
    if outcome is not None:
        trajectory.status = outcome.status
        submission = outcome.submission
        trajectory.steps.append(
            ToolStep(
                name="submit_plan",
                args={"ordered_ids": list(submission.ordered_ids)},
                result={"status": outcome.status, "problems": outcome.problems},
                error=None,
            )
        )
    return trajectory


CASES_SCHEMA = "quality/trajectory/1"
REQUIRED_CASE_KEYS = frozenset({"id", "why", "query", "script", "expect"})
OPTIONAL_CASE_KEYS = frozenset({"locale", "brief", "discover", "finish"})


class CaseError(ValueError):
    """A trajectory case that does not satisfy the documented schema."""


def validate_case(data: Any, path: Path | None = None) -> list[str]:
    """Every way one case violates the schema, as strings. [] == valid."""
    where = path.name if path is not None else "<memory>"
    errors: list[str] = []
    if not isinstance(data, dict):
        return [f"{where}: the case must be an object"]

    for key in sorted(set(data) - REQUIRED_CASE_KEYS - OPTIONAL_CASE_KEYS):
        errors.append(f"{where}: unknown key {key!r}")
    for key in sorted(REQUIRED_CASE_KEYS - set(data)):
        errors.append(f"{where}: missing required key {key!r}")

    for key in ("id", "why", "query"):
        if not isinstance(data.get(key), str) or not data.get(key, "").strip():
            errors.append(f"{where}: {key} must be a non-empty string")

    script = data.get("script")
    if not isinstance(script, list) or not script:
        errors.append(f"{where}: script must be a non-empty list of tool calls")
    else:
        for index, call in enumerate(script):
            if not isinstance(call, dict) or not isinstance(call.get("tool"), str):
                errors.append(f"{where}: script[{index}] must be {{tool, args}}")

    expect = data.get("expect")
    if not isinstance(expect, dict):
        errors.append(f"{where}: expect must be an object")
    elif not isinstance(expect.get("tools"), list) or not expect.get("tools"):
        errors.append(f"{where}: expect.tools must be a non-empty list of tool names")

    discover = data.get("discover")
    if discover is not None and (
        not isinstance(discover, dict)
        or not isinstance(discover.get("near"), list)
        or len(discover["near"]) != 2
        or "radius_km" not in discover
        or "limit" not in discover
    ):
        errors.append(f"{where}: discover must be {{near:[lat,lon], radius_km, limit}}")

    if ("@stops" in json.dumps(data) or "@stop" in json.dumps(data)) and discover is None:
        errors.append(f"{where}: a @stops/@stop placeholder needs a discover block")
    return errors


def load_cases(path: Path | None = None) -> list[dict[str, Any]]:
    """Load and validate every case (a malformed line is a loud error, never a skip)."""
    path = path or CASES
    cases: list[dict[str, Any]] = []
    problems: list[str] = []
    for lineno, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"{path.name}:{lineno}: invalid JSON ({exc})")
            continue
        errors = validate_case(data, path)
        if errors:
            problems.extend(f"{path.name}:{lineno}: {e}" for e in errors)
        else:
            cases.append(data)
    if problems:
        raise CaseError(
            f"{len(problems)} trajectory-schema violation(s):"
            + "".join(f"\n  - {p}" for p in problems)
        )
    if not cases:
        raise CaseError(f"no trajectory cases in {path}")
    return cases


def _repositories() -> Any:
    from db.store.areas import PostgresAreaRepository
    from db.store.places import PostgresPlaceRepository
    from db.store.registry import Repositories

    return Repositories(places=PostgresPlaceRepository(), areas=PostgresAreaRepository())


def run(cases: list[dict[str, Any]], repos: Any) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in cases:
        trajectory = run_case(case, repos)
        verdict = score_case(case, trajectory)
        results.append(
            {
                "id": case["id"],
                "why": case["why"],
                "passed": verdict["passed"],
                "failed": verdict["failed"],
                "checks": verdict["checks"],
                "observed_tools": trajectory.tool_names,
                "n_tool_calls": trajectory.n_tool_calls,
                "status": trajectory.status,
                "submitted": trajectory.submitted,
            }
        )
    return results


def report(results: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("")
    lines.append("ТРАЕКТОРИЯ              ВЫЗОВОВ  СТАТУС   ИТОГ   ПРОВАЛЫ")
    lines.append("─" * 72)
    for result in results:
        mark = "PASS" if result["passed"] else "FAIL"
        failures = ", ".join(result["failed"]) or "—"
        lines.append(
            f"{result['id']:<22} {result['n_tool_calls']:>7}  "
            f"{result['status'] or '—'!s:<7} {mark:<6} {failures}"
        )
        lines.append(f"{'':<22} тулзы: {' -> '.join(result['observed_tools']) or '—'}")
        if not result["passed"]:
            for name in result["failed"]:
                lines.append(f"      ✗ {name}: {result['checks'][name]['detail']}")
    n_passed = sum(1 for r in results if r["passed"])
    lines.append("─" * 72)
    lines.append(f"КЕЙСОВ ПРОШЛО: {n_passed}/{len(results)} — проверка кодом, не судьёй")
    lines.append("")
    return "\n".join(lines)


def write_report(results: list[dict[str, Any]], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": CASES_SCHEMA,
        "mode": "scripted",
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "n_cases": len(results),
            "n_cases_passed": sum(1 for r in results if r["passed"]),
            "n_cases_failed": sum(1 for r in results if not r["passed"]),
        },
        "cases": results,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"отчёт записан: {out}")
    return out


def _offline_reason(repos: Any) -> str | None:
    """Why the layer cannot run right now, or None when it can."""
    try:
        if not repos.places.ping():
            return "база данных не отвечает"
    except Exception as exc:
        return f"база данных недоступна: {type(exc).__name__}: {exc}"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trajectory evals for the planning agent")
    parser.add_argument("--json", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--case", action="append", help="only these case ids; repeatable")
    args = parser.parse_args(argv)

    cases = load_cases()
    if args.case:
        wanted = set(args.case)
        cases = [c for c in cases if c["id"] in wanted]
        missing = wanted - {c["id"] for c in cases}
        if missing:
            print(f"нет кейса: {sorted(missing)}", file=sys.stderr)
            return 1

    repos = _repositories()
    try:
        reason = _offline_reason(repos)
        if reason is not None:
            print(f"траектория не измерялась: {reason}", file=sys.stderr)
            print(
                "артефакт не записан — слой остаётся «НЕ ИЗМЕРЯЛОСЬ», а не зелёным",
                file=sys.stderr,
            )
            return 0
        results = run(cases, repos)
    finally:
        repos.close()

    print(report(results))
    write_report(results, args.json)
    return 1 if any(not r["passed"] for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
