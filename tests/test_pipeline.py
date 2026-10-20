"""Tests for the orchestration layer (extract -> validate -> investigate ->
persist) in isolation from any real LLM or database call -- extract.py,
validate.py, agent.py, and db.py each already have their own test suites
against the real thing (or mocks, for the hard cap). What's untested
elsewhere is pipeline.py's own wiring: does it call things in the right
order, does it skip investigation on a clean invoice, does it short-circuit
on an idempotent hit, and -- the actual bug this suite was written to catch
-- does the invoice get inserted with a decision the schema's CHECK
constraint allows (see decisions_log_full.md section 5: the original draft
inserted a 'PENDING' placeholder, which doesn't exist in the schema)."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from invoice_agent import pipeline  # noqa: E402
from invoice_agent.extract import ExtractionOutcome  # noqa: E402
from invoice_agent.idempotency import IdempotencyStore  # noqa: E402
from invoice_agent.validate import ValidationResult  # noqa: E402


@pytest.fixture
def idempotency(tmp_path):
    return IdempotencyStore(tmp_path / "idempotency.sqlite3")


@pytest.fixture
def fake_file(tmp_path):
    p = tmp_path / "invoice.pdf"
    p.write_bytes(b"fake pdf bytes")
    return p


def _mock_validator(decision, exception_codes, details=None):
    v = MagicMock()
    v.validate.return_value = ValidationResult(decision, exception_codes, details or {})
    return v


# =============================================================== clean path
def test_clean_invoice_skips_investigation_and_records_auto_approve(monkeypatch, fake_file, idempotency):
    monkeypatch.setattr(pipeline, "extract_invoice_file", lambda path, client=None:
                         ExtractionOutcome(status="ok", fields={"lines": []}, confidence={}, attempts=1))
    investigator = MagicMock()

    result = pipeline.process_invoice(fake_file, _mock_validator("AUTO_APPROVE", []), investigator, idempotency)

    assert result.decision == "AUTO_APPROVE"
    assert result.exception_codes == []
    assert result.investigated is False
    investigator.investigate.assert_not_called()


# ============================================================ exception path
def test_exception_invoice_triggers_investigation(monkeypatch, fake_file, idempotency):
    monkeypatch.setattr(pipeline, "extract_invoice_file", lambda path, client=None:
                         ExtractionOutcome(status="ok", fields={"lines": []}, confidence={}, attempts=1))
    investigator = MagicMock()
    investigator.investigate.return_value = MagicMock(explanation="explained", suggested_action="check it",
                                                        to_dict=lambda: {}, model="claude-sonnet-5")

    result = pipeline.process_invoice(fake_file, _mock_validator("EXCEPTION", ["PRICE_VARIANCE"]),
                                       investigator, idempotency)

    assert result.decision == "EXCEPTION"
    assert result.investigated is True
    investigator.investigate.assert_called_once()


def test_investigate_exceptions_false_skips_agent_even_on_exception(monkeypatch, fake_file, idempotency):
    monkeypatch.setattr(pipeline, "extract_invoice_file", lambda path, client=None:
                         ExtractionOutcome(status="ok", fields={"lines": []}, confidence={}, attempts=1))
    investigator = MagicMock()

    result = pipeline.process_invoice(fake_file, _mock_validator("EXCEPTION", ["PRICE_VARIANCE"]),
                                       investigator, idempotency, investigate_exceptions=False)

    assert result.investigated is False
    investigator.investigate.assert_not_called()


# ========================================================== extraction failure
def test_extraction_failure_routes_to_exception_without_running_validator(monkeypatch, fake_file, idempotency):
    monkeypatch.setattr(pipeline, "extract_invoice_file", lambda path, client=None:
                         ExtractionOutcome(status="failed", exception_code="EXTRACTION_FAILED", attempts=3))
    validator = MagicMock()

    investigator = MagicMock()
    result = pipeline.process_invoice(fake_file, validator, investigator, idempotency)

    assert result.decision == "EXCEPTION"
    assert result.exception_codes == ["EXTRACTION_FAILED"]
    validator.validate.assert_not_called()
    # Regression guard: an extraction failure must not also trigger the
    # investigation agent -- there are no fields to investigate, and this
    # wasted real API spend in section 6's eval run before being fixed.
    investigator.investigate.assert_not_called()
    assert result.investigated is False


# =============================================================== idempotency
def test_second_call_on_same_bytes_is_a_cache_hit_and_skips_extraction(monkeypatch, fake_file, idempotency):
    calls = {"n": 0}

    def fake_extract(path, client=None):
        calls["n"] += 1
        return ExtractionOutcome(status="ok", fields={"lines": []}, confidence={}, attempts=1)

    monkeypatch.setattr(pipeline, "extract_invoice_file", fake_extract)
    validator = _mock_validator("AUTO_APPROVE", [])

    r1 = pipeline.process_invoice(fake_file, validator, MagicMock(), idempotency)
    r2 = pipeline.process_invoice(fake_file, validator, MagicMock(), idempotency)

    assert calls["n"] == 1
    assert r1.idempotent_hit is False
    assert r2.idempotent_hit is True
    assert r2.decision == r1.decision


# ===================================================== decision/schema safety
def test_decision_passed_to_db_insert_is_always_a_schema_valid_value(monkeypatch, fake_file, idempotency):
    """Direct regression test for the bug this module was rewritten to fix:
    insert_invoice must be called with a decision the ap.invoices CHECK
    constraint actually allows (AUTO_APPROVE/EXCEPTION/APPROVED/REJECTED),
    never a placeholder like 'PENDING' invented before validation runs."""
    monkeypatch.setattr(pipeline, "extract_invoice_file", lambda path, client=None:
                         ExtractionOutcome(status="ok", fields={"lines": []}, confidence={}, attempts=1))

    fake_conn = MagicMock()
    fake_db = MagicMock()
    fake_db.insert_invoice.return_value = 1
    monkeypatch.setattr(pipeline, "db", fake_db)

    pipeline.process_invoice(fake_file, _mock_validator("AUTO_APPROVE", []), MagicMock(),
                              idempotency, conn=fake_conn)

    _, kwargs = fake_db.insert_invoice.call_args
    passed_decision = kwargs.get("decision", fake_db.insert_invoice.call_args[0][-1] if not kwargs else None)
    assert passed_decision in ("AUTO_APPROVE", "EXCEPTION", "APPROVED", "REJECTED")
