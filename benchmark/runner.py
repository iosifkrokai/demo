"""Benchmark runner: runs all cases and scores them.

Usage:
    python -m benchmark.runner
    python -m benchmark.runner --query "парки и скверы"   # run single case
    python -m benchmark.runner --report                      # generate report
"""

import argparse
import json
import sys
import time
from pathlib import Path

# Allow running as module from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests

from benchmark.cases import CASES, BenchmarkCase

AGENT_URL = "http://localhost:8080"


def run_case(case: BenchmarkCase) -> dict:
    req = {"query": case.query, "n_points": case.n_points}
    if case.time_budget:
        req["time_budget_minutes"] = case.time_budget

    start = time.perf_counter()
    try:
        r = requests.post(f"{AGENT_URL}/routes/generate", json=req, timeout=45)
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        if not r.ok:
            return {
                "query": case.query,
                "description": case.description,
                "status": "error",
                "status_code": r.status_code,
                "error": r.text[:200],
                "elapsed_ms": elapsed_ms,
            }

        d = r.json()
        pts = d.get("points", [])
        parsed = d.get("parsed", {})
        budget = d.get("budget")
        shape = d.get("shape", {})

        # ── Score ──
        score = 0
        issues: list[str] = []
        details: dict = {}

        # 1. Place count
        min_n, max_n = case.golden_place_count
        if min_n <= len(pts) <= max_n:
            score += 2
        elif len(pts) >= 2:
            score += 1
            issues.append(f"place_count:{len(pts)} (expected {min_n}-{max_n})")
        else:
            issues.append(f"too_few_places:{len(pts)}")

        # 2. Category match
        if case.golden_categories:
            place_cats = [p.get("category") for p in pts]
            matched = [c for c in case.golden_categories if c in place_cats]
            recall = len(matched) / len(case.golden_categories)
            if recall >= 0.75:
                score += 3
            elif recall >= 0.5:
                score += 1
                issues.append(f"cat_recall:{recall:.0%} ({matched})")
            else:
                issues.append(f"cat_recall:{recall:.0%} (got {place_cats}, expected {case.golden_categories})")
            details["matched_categories"] = matched
            details["place_categories"] = place_cats
        else:
            # Any mix is fine for broad queries
            score += 2

        # 3. Budget returned when requested
        if case.time_budget and budget:
            score += 1
        elif case.time_budget and not budget:
            issues.append("budget_not_returned")
        else:
            score += 1  # no budget requested, no penalty

        # 4. Shape present
        if shape and shape.get("coordinates"):
            score += 1
        else:
            issues.append("no_shape")

        verdict = "pass" if score >= 7 else "warn" if score >= 4 else "fail"

        return {
            "query": case.query,
            "description": case.description,
            "status": verdict,
            "score": score,
            "max_score": 9,
            "elapsed_ms": elapsed_ms,
            "place_count": len(pts),
            "place_names": [p["name"] for p in pts],
            "place_categories": [p.get("category") for p in pts],
            "parsed_categories": parsed.get("categories", []),
            "requested_categories": case.expected_categories,
            "golden_categories": case.golden_categories,
            "budget": budget,
            "issues": issues,
            "details": details,
        }

    except requests.exceptions.Timeout:
        return {
            "query": case.query,
            "description": case.description,
            "status": "timeout",
            "elapsed_ms": int((time.perf_counter() - start) * 1000),
        }
    except Exception as e:
        return {
            "query": case.query,
            "description": case.description,
            "status": "error",
            "error": str(e),
            "elapsed_ms": int((time.perf_counter() - start) * 1000),
        }


def run_all(filter_query: str | None = None) -> list[dict]:
    results = []
    cases_to_run = CASES if not filter_query else [c for c in CASES if filter_query.lower() in c.query.lower()]
    if not cases_to_run:
        print(f"No cases match filter: {filter_query!r}")
        return []

    print(f"Running {len(cases_to_run)} benchmark cases against {AGENT_URL}...")
    print("=" * 80)

    for i, case in enumerate(cases_to_run, 1):
        r = run_case(case)
        results.append(r)

        icon = {"pass": "✓", "warn": "~", "fail": "✗", "error": "!", "timeout": "?"}.get(r["status"], "?")
        score_str = f"{r.get('score', 0)}/9" if "score" in r else r["status"]
        print(f"  [{icon}] {case.query!r:40s} {score_str:8s}  ({r.get('elapsed_ms', 0)}ms)")

        if r.get("issues"):
            for issue in r["issues"]:
                print(f"       └─ {issue}")

        if r.get("place_names"):
            cats = r.get("place_categories", [])
            names = r.get("place_names", [])
            for j, (n, c) in enumerate(zip(names, cats)):
                print(f"       {j+1}. [{c or '?'}] {n}")
        print()

    return results


def report(results: list[dict]) -> None:
    total = len(results)
    by_status = {"pass": 0, "warn": 0, "fail": 0, "error": 0, "timeout": 0}
    scores = [r["score"] for r in results if "score" in r]
    avg_ms = sum(r["elapsed_ms"] for r in results) / max(len(results), 1)

    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    print("=" * 80)
    print("BENCHMARK SUMMARY")
    print("=" * 80)
    print(f"  Total cases:     {total}")
    print(f"  ✓ Pass:           {by_status['pass']} ({100*by_status['pass']/max(total,1):.0f}%)")
    print(f"  ~ Warn:           {by_status['warn']} ({100*by_status['warn']/max(total,1):.0f}%)")
    print(f"  ✗ Fail:           {by_status['fail']} ({100*by_status['fail']/max(total,1):.0f}%)")
    if scores:
        print(f"  Score range:     {min(scores)} – {max(scores)} (avg {sum(scores)/len(scores):.1f}/9)")
    print(f"  Avg latency:     {avg_ms:.0f}ms")
    print()

    # Failed cases
    failed = [r for r in results if r["status"] in ("fail", "error", "timeout")]
    if failed:
        print("FAILED / ERROR:")
        for r in failed:
            print(f"  • {r['query']}: {r.get('error') or r.get('issues', ['unknown'])}, score={r.get('score', '?')}")
        print()

    # Category mismatch cases
    cat_mismatch = [r for r in results if any("cat_recall" in i for i in r.get("issues", []))]
    if cat_mismatch:
        print("CATEGORY MISMATCH (LLM parsing issue):")
        for r in cat_mismatch:
            parsed = r.get("parsed_categories", [])
            expected = r.get("golden_categories", [])
            got = r.get("place_categories", [])
            print(f"  • {r['query']!r}")
            print(f"    LLM parsed:  {parsed}")
            print(f"    Golden cats: {expected}")
            print(f"    Got places:  {got}")
        print()


def main():
    parser = argparse.ArgumentParser(description="Grodno route agent benchmark")
    parser.add_argument("--query", help="Filter to specific query substring")
    parser.add_argument("--report", action="store_true", help="Show summary report")
    parser.add_argument("--json", help="Write results to JSON file")
    args = parser.parse_args()

    results = run_all(filter_query=args.query)

    if args.report:
        report(results)

    if args.json and results:
        p = Path(args.json)
        p.write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(f"Results → {p}")


if __name__ == "__main__":
    main()
