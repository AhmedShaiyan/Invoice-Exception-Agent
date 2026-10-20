"""Cross-check that compute_summary()'s JSON numbers agree with
build_report()'s markdown -- the two are deliberately separate functions
(see compute_summary's docstring), so nothing enforces agreement except a
test like this one."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import evaluate  # noqa: E402

ROW = {
    "file": "TEST.pdf", "scenario": "test", "layout": "classic",
    "expected_decision": "EXCEPTION", "actual_decision": "EXCEPTION",
    "expected_codes": ["QTY_EXCEEDS_GR"], "actual_codes": ["QTY_EXCEEDS_GR"],
    "extraction_status": "ok", "field_correct": 8, "field_total": 10,
    "injection_attempted": False, "investigated": True, "grounded": True,
    "ungrounded_numbers": [], "elapsed_seconds": 1.5,
    "extraction_usage": {"input_tokens": 100, "output_tokens": 50},
    "investigation_usage": {"input_tokens": 200, "output_tokens": 100},
    "extraction_model": "claude-haiku-4-5", "investigation_model": "claude-sonnet-5",
}


def test_false_approvals_agree_between_report_and_summary():
    rows = [ROW, {**ROW, "file": "FA.pdf", "expected_decision": "EXCEPTION",
                  "actual_decision": "AUTO_APPROVE", "actual_codes": []}]
    report = evaluate.build_report(rows)
    summary = evaluate.compute_summary(rows)
    assert summary["false_approvals"] == 1
    assert "False approvals: 1 /" in report


def test_accuracy_overall_agrees_between_report_and_summary():
    rows = [ROW, {**ROW, "file": "B.pdf", "field_correct": 5, "field_total": 5}]
    report = evaluate.build_report(rows)
    summary = evaluate.compute_summary(rows)
    overall = summary["accuracy_overall"]
    assert overall["correct"] == 13
    assert overall["total"] == 15
    assert f"{overall['correct']} / {overall['total']}" in report


def test_timing_none_when_no_rows_have_elapsed_seconds():
    """Regression test: _mean([])/_median([]) used to silently return 0.0,
    which build_report() then displayed as a real "0.00s" measurement
    indistinguishable from genuinely fast processing. compute_summary()
    must represent "no data" as None, not a fabricated zero."""
    rows = [{**ROW, "elapsed_seconds": None}]
    summary = evaluate.compute_summary(rows)
    assert summary["timing"]["no_investigation"] is None
    assert summary["timing"]["with_investigation"] is None
    report = evaluate.build_report(rows)
    assert "0.00s mean" not in report
    assert "no timing data" in report
