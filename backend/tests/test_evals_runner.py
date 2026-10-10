"""The stage-eval runner itself: it must not lie about its own results.

A broken case file is loud, and a documented gap is reported but never fails.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from quality import evals


def test_a_broken_case_file_is_a_loud_error(tmp_path, monkeypatch):
    bad = tmp_path / "cases"
    bad.mkdir()
    (bad / "verdicts.jsonl").write_text("{not json}\n", encoding="utf-8")
    monkeypatch.setattr(evals, "CASES", bad)

    try:
        evals._load("verdicts")
    except SystemExit as exc:
        assert "verdicts.jsonl:1" in str(exc)
    else:  # pragma: no cover — the guard is the point of the test
        raise AssertionError("сломанный кейс обязан падать, а не исчезать")


def test_a_known_gap_is_reported_but_does_not_fail_the_run():
    checks = [
        {"case": "a", "check": "verdict", "ok": True, "detail": "", "why": ""},
        {"case": "b", "check": "verdict", "ok": False, "detail": "", "why": "",
         "known_gap": True},
    ]
    passed, total = evals._rate(checks)
    assert (passed, total) == (1, 1), "пробел не должен попадать в знаменатель"


def test_every_case_says_why_it_exists():
    for stage in ("verdicts", "services_along", "interpretation"):
        for case in evals._load(stage):
            assert case.why.strip(), f"{stage}/{case.id}: кейс без объяснения"


def test_the_offline_stages_pass_here():
    results = [evals.run_verdicts(), evals.run_services()]
    for res in results:
        if res.get("skipped"):
            continue
        failed = [
            c for c in res["checks"] if not c["ok"] and not c.get("known_gap")
        ]
        assert failed == [], f"{res['stage']}: {json.dumps(failed, ensure_ascii=False)}"
