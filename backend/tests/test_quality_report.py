"""The quality page must read the numbers the runner actually writes."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from quality.report import read_golden


def _report() -> dict:
    """A minimal artifact in the runner's own shape."""
    return {
        "schema": "bench_routes/report/2",
        "mode": "golden-live",
        "summary": {
            "n_cases": 2,
            "n_cases_passed": 1,
            "n_cases_failed": 1,
            "compliance_rate": 0.5,
            "failures_by_reason": {"wrong_status": 1},
            "unverified_checks": ["x:result_mode_mismatch"],
        },
        "cases": [
            {
                "id": "ok_case",
                "query": "замки Гродно",
                "runs": [{"verdict": {"passed": True, "reason": "ok", "checks": {}}}],
            },
            {
                "id": "bad_case",
                "query": "Вильнюс",
                "runs": [
                    {
                        "verdict": {
                            "passed": False,
                            "reason": "wrong_status",
                            "detail": "status ready is not one of ['infeasible']",
                            "checks": {
                                "wrong_status": {"ok": False, "detail": "status ready"},
                                "in_region": {"ok": True, "detail": "ok"},
                            },
                        }
                    }
                ],
            },
        ],
    }


def test_read_golden_reads_the_reports_own_keys(tmp_path):
    path = tmp_path / "compliance.json"
    path.write_text(json.dumps(_report(), ensure_ascii=False), encoding="utf-8")

    data = read_golden(path)

    assert data["measured"] is True
    assert data["compliance"] == 0.5
    assert (data["passed"], data["total"]) == (1, 2)
    assert data["failure_reasons"] == {"wrong_status": 1}
    assert data["unverified"] == ["x:result_mode_mismatch"]
    assert [c["case"] for c in data["failed_cases"]] == ["bad_case"]
    assert "wrong_status" in data["failed_cases"][0]["detail"]


def test_read_golden_finds_the_report_at_the_default_path(tmp_path, monkeypatch):
    """A run written to the default path must not read as «не измерялось».
    Nothing generated is committed any more, so the artifact is staged here.
    """
    report = tmp_path / "compliance.json"
    report.write_text(json.dumps(_report(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("quality.report.COMPLIANCE", report)
    monkeypatch.setattr("quality.report.SNAPSHOTS", tmp_path / "snapshots")

    data = read_golden()

    assert data["measured"] is True, data.get("how")
    assert data["source"].endswith("compliance.json")
    assert isinstance(data["passed"], int)
    assert isinstance(data["total"], int)
    assert data["passed"] <= data["total"]


def test_read_golden_without_any_report_says_how_to_make_one(tmp_path, monkeypatch):
    monkeypatch.setattr("quality.report.COMPLIANCE", tmp_path / "missing.json")
    monkeypatch.setattr("quality.report.SNAPSHOTS", tmp_path / "snapshots")

    data = read_golden()

    assert data["measured"] is False
    assert "-m quality --golden" in data["how"]
