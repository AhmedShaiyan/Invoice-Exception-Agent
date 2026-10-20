"""Tests for the deterministic parts of extraction: money-string
sanitization, e-invoice JSON parsing, and idempotency. Deliberately does NOT
make live API calls in this suite -- that would make every test run cost
money and be flaky on rate limits/network. The PDF->Claude path was verified
manually against real invoices spanning clean/scanned/messy-scan/
prompt-injection scenarios (see docs/decisions_log_full.md section 3) and is
exercised at full scale by section 6's evaluation run."""
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from invoice_agent import codes as C  # noqa: E402
from invoice_agent.extract import (  # noqa: E402
    ExtractionOutcome, _has_text_layer, _sanitize_money_strings, extract_from_einvoice,
)
from invoice_agent.idempotency import IdempotencyStore  # noqa: E402

SAP_DIR = ROOT / "data" / "sap"
INVOICES_DIR = ROOT / "data" / "invoices"


# =========================================================== text-layer routing
def test_has_text_layer_true_for_typed_pdf():
    """Regression guard for the model-routing decision in section 6: a
    small live sample found Haiku 4.5 badly wrong (and overconfident) on
    the dataset's messy_scan invoices, so typed vs. image-only PDFs now
    route to different models. Uses a real generated invoice, not a
    synthetic fixture -- this is exactly the distinction production
    extraction relies on."""
    gt = json.loads((INVOICES_DIR / "ground_truth.json").read_text())
    typed_file = next(e["file"] for e in gt if e["layout"] == "tabular")
    assert _has_text_layer((INVOICES_DIR / typed_file).read_bytes()) is True


def test_has_text_layer_false_for_scanned_pdf():
    gt = json.loads((INVOICES_DIR / "ground_truth.json").read_text())
    scanned_file = next(e["file"] for e in gt if e["layout"] in ("scanned", "messy_scan"))
    assert _has_text_layer((INVOICES_DIR / scanned_file).read_bytes()) is False


def test_has_text_layer_false_for_garbage_bytes():
    """Unreadable/corrupt input routes to the stronger model (fail toward
    caution), not a crash."""
    assert _has_text_layer(b"not a pdf at all") is False


# ============================================================== sanitization
def test_sanitize_strips_thousands_separators():
    """Reproduces the exact failure observed live: a scanned invoice came
    back with "3,633.20" -- Decimal() raises InvalidOperation on that, which
    would otherwise crash the validator outright (not just mis-score a
    field) the first time a real invoice triggered it."""
    fields = {
        "subtotal": "3,633.20", "gst_amount": "326.99", "total": "3,960.19",
        "lines": [{"unit_price": "12.40", "amount": "1,234.56"}],
    }
    clean = _sanitize_money_strings(fields)
    assert clean["subtotal"] == "3633.20"
    assert clean["total"] == "3960.19"
    assert clean["lines"][0]["amount"] == "1234.56"
    # must be directly Decimal-parseable after sanitizing, not just "look right"
    Decimal(clean["subtotal"])
    Decimal(clean["total"])
    Decimal(clean["lines"][0]["amount"])


def test_sanitize_strips_currency_symbols_and_whitespace():
    fields = {"subtotal": "$ 1513.40", "gst_amount": "136.21", "total": "1649.61", "lines": []}
    clean = _sanitize_money_strings(fields)
    assert clean["subtotal"] == "1513.40"
    Decimal(clean["subtotal"])


def test_sanitize_leaves_clean_values_untouched():
    fields = {"subtotal": "68.00", "gst_amount": "6.12", "total": "74.12",
              "lines": [{"unit_price": "6.80", "amount": "68.00"}]}
    clean = _sanitize_money_strings(dict(fields))
    assert clean == fields


# ============================================================= e-invoice JSON
def test_extract_from_einvoice_real_sample():
    """Parses one of the dataset's actual generated e-invoice files (no
    mocking) and confirms the mapped fields match what generate_invoices.py
    put in, and that the result validates cleanly through the engine."""
    sample = next(ROOT.glob("data/invoices/*.einvoice.json"), None)
    assert sample is not None, "no e-invoice samples found -- run scripts/generate_invoices.py first"

    gt = {e["file"]: e for e in json.loads((ROOT / "data/invoices/ground_truth.json").read_text())}
    entry = gt[sample.name]

    outcome = extract_from_einvoice(sample.read_bytes())
    assert outcome.status == "ok"
    assert outcome.confidence == {"vendor_and_po": 1.0, "line_items": 1.0, "totals": 1.0,
                                   "notes": "structured e-invoice input, no OCR uncertainty"}
    assert outcome.fields["invoice_number"] == entry["fields"]["invoice_number"]
    assert Decimal(outcome.fields["total"]) == Decimal(entry["fields"]["total"])
    assert len(outcome.fields["lines"]) == len(entry["fields"]["lines"])

    from invoice_agent.validate import Validator
    validator = Validator.from_csvs(SAP_DIR)
    result = validator.validate(outcome.fields)
    assert result.decision == entry["expected_decision"]
    assert result.exception_codes == sorted(entry["expected_exceptions"])


def test_extract_from_einvoice_malformed_json_routes_to_review():
    outcome = extract_from_einvoice(b"{not valid json")
    assert outcome.status == "failed"
    assert outcome.exception_code == C.EXTRACTION_FAILED


def test_extract_from_einvoice_missing_required_field_routes_to_review():
    outcome = extract_from_einvoice(json.dumps({"ID": "X"}).encode())
    assert outcome.status == "failed"
    assert outcome.exception_code == C.EXTRACTION_FAILED


# ================================================================ idempotency
@pytest.fixture
def store(tmp_path):
    return IdempotencyStore(tmp_path / "test_idempotency.sqlite3")


def test_process_once_runs_compute_fn_only_on_first_call(store):
    calls = []

    def compute():
        calls.append(1)
        return {"status": "ok", "fields": {"x": 1}}

    content = b"same invoice bytes"
    outcome1, cached1 = store.process_once(content, "invoice.pdf", compute)
    outcome2, cached2 = store.process_once(content, "invoice.pdf", compute)

    assert cached1 is False
    assert cached2 is True
    assert outcome1 == outcome2
    assert len(calls) == 1   # compute_fn only ran once


def test_process_once_different_content_runs_separately(store):
    calls = []

    def compute():
        calls.append(1)
        return {"status": "ok", "n": len(calls)}

    store.process_once(b"invoice A", "a.pdf", compute)
    store.process_once(b"invoice B", "b.pdf", compute)

    assert len(calls) == 2


def test_process_once_same_content_different_filename_is_still_cached(store):
    """Idempotency is keyed by content hash, not filename -- a renamed or
    re-uploaded copy of the exact same invoice must not be reprocessed."""
    calls = []

    def compute():
        calls.append(1)
        return {"status": "ok"}

    content = b"identical bytes"
    store.process_once(content, "original_name.pdf", compute)
    _, cached = store.process_once(content, "renamed_copy.pdf", compute)

    assert cached is True
    assert len(calls) == 1


def test_outcome_to_dict_roundtrips_through_json():
    outcome = ExtractionOutcome(status="ok", fields={"a": "1"}, confidence={"vendor_and_po": 0.9,
                                 "line_items": 0.9, "totals": 0.9, "notes": ""}, attempts=1)
    roundtripped = json.loads(json.dumps(outcome.to_dict()))
    assert roundtripped["status"] == "ok"
    assert roundtripped["fields"] == {"a": "1"}
