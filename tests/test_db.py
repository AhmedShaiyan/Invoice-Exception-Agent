"""Tests for Postgres persistence (ap.invoices / ap.exceptions / ap.audit_log).

Unlike test_extract.py and test_agent.py, these hit a *real* local Postgres
(docker-compose, port 5433) rather than mocking -- there's no LLM involved
and no cost/flakiness risk, so the same "verify against the real thing"
standard applies as it does to validate.py's tests against real CSVs. If
the database isn't reachable, every test here skips with a clear reason
rather than failing noisily -- a missing local Docker container is an
environment problem, not a code regression.

All test rows are tagged with a 'test_db_' source_file prefix and swept up
in a fixture teardown that runs even if a test raises -- ON DELETE CASCADE
on ap.exceptions/ap.audit_log means deleting the invoice row is enough.
"""
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from invoice_agent import codes as C  # noqa: E402
from invoice_agent import db  # noqa: E402

SOURCE_PREFIX = "test_db_"


@pytest.fixture
def conn():
    try:
        c = db.connect()
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not reachable (is docker-compose up? {e})")
    yield c
    c.rollback()  # clear any aborted transaction before cleanup
    with c.cursor() as cur:
        cur.execute("DELETE FROM ap.invoices WHERE source_file LIKE %s", (f"{SOURCE_PREFIX}%",))
    c.commit()
    c.close()


def _fields(**overrides):
    base = {
        "invoice_number": "TST-0001", "invoice_date": "2026-01-15",
        "vendor_name": "Test Vendor Pte Ltd", "vendor_gst_reg_no": "201800000X",
        "po_number": "4500099999", "currency": "SGD",
        "lines": [{"po_item": "00010", "material": "MAT-9999", "description": "Test widget",
                    "quantity": 10, "unit": "EA", "unit_price": "10.00", "amount": "100.00"}],
        "subtotal": "100.00", "gst_rate": "0.09", "gst_amount": "9.00", "total": "109.00",
    }
    base.update(overrides)
    return base


def _source(name):
    return f"{SOURCE_PREFIX}{name}"


# ================================================================= invoices
def test_insert_invoice_round_trips_fields(conn):
    invoice_id = db.insert_invoice(conn, _source("roundtrip.pdf"), _fields(), decision="AUTO_APPROVE")
    with conn.cursor() as cur:
        cur.execute("SELECT vendor_name, invoice_number, total, decision FROM ap.invoices WHERE id = %s",
                    (invoice_id,))
        row = cur.fetchone()
    assert row == ("Test Vendor Pte Ltd", "TST-0001", 109.00, "AUTO_APPROVE")


def test_insert_invoice_persists_lifnr(conn):
    """Regression test: insert_invoice used to silently drop the validator's
    resolved vendor match (ValidationResult.matched_vendor) -- the lifnr
    column existed in the schema but nothing ever wrote to it, so every
    row's lifnr was NULL and any join against sap.lfa1 (e.g. for payment
    terms) could never match."""
    invoice_id = db.insert_invoice(conn, _source("lifnr.pdf"), _fields(), decision="AUTO_APPROVE",
                                    lifnr="100001")
    with conn.cursor() as cur:
        cur.execute("SELECT lifnr FROM ap.invoices WHERE id = %s", (invoice_id,))
        assert cur.fetchone() == ("100001",)


def test_insert_invoice_handles_missing_fields_for_failed_extraction(conn):
    """Extraction failures carry no fields dict at all (see pipeline.py) --
    insert_invoice must still succeed with every column NULL, not crash."""
    invoice_id = db.insert_invoice(conn, _source("failed.pdf"), {}, decision="EXCEPTION")
    with conn.cursor() as cur:
        cur.execute("SELECT vendor_name, total FROM ap.invoices WHERE id = %s", (invoice_id,))
        row = cur.fetchone()
    assert row == (None, None)


def test_insert_invoice_rejects_invalid_decision_via_check_constraint(conn):
    """Regression guard for the exact bug caught before this suite existed:
    pipeline.py originally tried to insert a 'PENDING' placeholder decision,
    which the schema's CHECK constraint has never allowed. This confirms
    the constraint is actually live in the running database, not just in
    schema.sql on disk."""
    with pytest.raises(psycopg.errors.CheckViolation):
        db.insert_invoice(conn, _source("bad_decision.pdf"), _fields(), decision="PENDING")


# =============================================================== exceptions
def test_insert_exceptions_stores_severity_detail_and_explanation(conn):
    invoice_id = db.insert_invoice(conn, _source("exc.pdf"), _fields(), decision="EXCEPTION")
    db.insert_exceptions(conn, invoice_id, [C.PRICE_VARIANCE, C.QTY_EXCEEDS_GR],
                          detail_by_code={C.PRICE_VARIANCE: [{"invoiced_price": "12.00"}]},
                          explanation="Price is 20% over PO.", suggested_action="Confirm with vendor.")
    with conn.cursor() as cur:
        cur.execute("SELECT code, severity, detail, explanation, suggested_action FROM ap.exceptions "
                     "WHERE invoice_id = %s ORDER BY code", (invoice_id,))
        rows = cur.fetchall()
    by_code = {r[0]: r for r in rows}
    assert by_code[C.PRICE_VARIANCE][1] == C.SEVERITY[C.PRICE_VARIANCE] == "HIGH"
    assert by_code[C.PRICE_VARIANCE][2] == [{"invoiced_price": "12.00"}]
    assert by_code[C.PRICE_VARIANCE][3] == "Price is 20% over PO."
    assert by_code[C.QTY_EXCEEDS_GR][1] == C.SEVERITY[C.QTY_EXCEEDS_GR] == "MEDIUM"
    assert by_code[C.QTY_EXCEEDS_GR][2] == {}   # no detail provided for this code -> defaults to {}


# ================================================================= audit log
def test_log_audit_and_get_audit_trail_preserves_order(conn):
    invoice_id = db.insert_invoice(conn, _source("audit.pdf"), _fields(), decision="EXCEPTION")
    db.log_audit(conn, invoice_id, "agent:extractor", "extract", payload={"status": "ok"}, model="claude-sonnet-5")
    db.log_audit(conn, invoice_id, "agent:validator", "validate", payload={"decision": "EXCEPTION"})
    db.log_audit(conn, invoice_id, "agent:investigator", "investigate", payload={"root_cause": "x"},
                 model="claude-sonnet-5")

    trail = db.get_audit_trail(conn, invoice_id)
    assert [e["action"] for e in trail] == ["extract", "validate", "investigate"]
    assert trail[0]["model"] == "claude-sonnet-5"
    assert trail[1]["payload"] == {"decision": "EXCEPTION"}


def test_record_human_decision_updates_invoice_and_appends_audit_entry(conn):
    invoice_id = db.insert_invoice(conn, _source("human.pdf"), _fields(), decision="EXCEPTION")
    db.record_human_decision(conn, invoice_id, "APPROVED", reviewer="shaiyan", reason="Price confirmed with vendor.")

    with conn.cursor() as cur:
        cur.execute("SELECT decision FROM ap.invoices WHERE id = %s", (invoice_id,))
        assert cur.fetchone() == ("APPROVED",)

    trail = db.get_audit_trail(conn, invoice_id)
    human_entries = [e for e in trail if e["actor"] == "user:shaiyan"]
    assert len(human_entries) == 1
    assert human_entries[0]["action"] == "approved"
    assert human_entries[0]["payload"] == {"reason": "Price confirmed with vendor."}


def test_record_human_decision_rejects_invalid_decision_value(conn):
    invoice_id = db.insert_invoice(conn, _source("human_invalid.pdf"), _fields(), decision="EXCEPTION")
    with pytest.raises(ValueError):
        db.record_human_decision(conn, invoice_id, "MAYBE", reviewer="shaiyan", reason="unsure")

    with conn.cursor() as cur:
        cur.execute("SELECT decision FROM ap.invoices WHERE id = %s", (invoice_id,))
        assert cur.fetchone() == ("EXCEPTION",)   # unchanged
    assert db.get_audit_trail(conn, invoice_id) == []   # no partial audit entry either


# =============================================== read queries (app) / quota
def test_dashboard_stats_counts_by_decision(conn):
    db.insert_invoice(conn, _source("stats_a.pdf"), _fields(), decision="AUTO_APPROVE")
    db.insert_invoice(conn, _source("stats_b.pdf"), _fields(invoice_number="TST-0002"), decision="EXCEPTION")
    stats = db.dashboard_stats(conn)
    assert stats["total"] >= 2
    assert stats["by_decision"].get("AUTO_APPROVE", 0) >= 1
    assert stats["by_decision"].get("EXCEPTION", 0) >= 1


def test_list_invoices_filters_by_decision(conn):
    db.insert_invoice(conn, _source("list_a.pdf"), _fields(), decision="AUTO_APPROVE")
    db.insert_invoice(conn, _source("list_b.pdf"), _fields(invoice_number="TST-0002"), decision="EXCEPTION")
    exceptions_only = db.list_invoices(conn, decision="EXCEPTION")
    assert all(r["decision"] == "EXCEPTION" for r in exceptions_only)
    assert any(r["source_file"] == _source("list_b.pdf") for r in exceptions_only)
    assert not any(r["source_file"] == _source("list_a.pdf") for r in exceptions_only)


def test_vendor_scorecard_groups_by_resolved_vendor_not_raw_text(conn):
    """Regression test: a real vendor whose name was extracted with
    different case on different invoices (e.g. "Redwood Overseas
    Equipment Inc" vs "REDWOOD OVERSEAS EQUIPMENT INC") used to show up
    as two separate scorecard rows -- found live while visually checking
    the Insights page. Both invoices share the same resolved lifnr, so
    they must combine into one row. Uses a fictitious lifnr (not in
    sap.lfa1, so the name falls back to the raw text) to avoid colliding
    with real vendors' real exception counts already in the database --
    this table isn't swept by the SOURCE_PREFIX teardown's WHERE clause,
    since vendor_scorecard deliberately has no source_file filter. Also
    guarantees enough occurrences to rank inside the function's own
    LIMIT 10, regardless of how much real data happens to be loaded --
    reads the current top count first rather than hard-coding a number
    that could go stale."""
    current_max = max((r["exception_count"] for r in db.vendor_scorecard(conn)), default=0)
    names = ["Zzz Test Vendor Pte Ltd", "ZZZ TEST VENDOR PTE LTD"]
    for i in range(current_max + 1):
        invoice_id = db.insert_invoice(conn, _source(f"vendor_{i}.pdf"),
                                        _fields(vendor_name=names[i % 2], invoice_number=f"TST-{i:04d}"),
                                        decision="EXCEPTION", lifnr="999999")
        db.insert_exceptions(conn, invoice_id, [C.PRICE_VARIANCE])
    scorecard = db.vendor_scorecard(conn)
    matches = [r for r in scorecard if "zzz test vendor" in r["vendor_name"].lower()]
    assert len(matches) == 1, f"expected one combined row, got {matches}"
    assert matches[0]["exception_count"] == current_max + 1


def test_get_invoice_and_get_exceptions_round_trip(conn):
    invoice_id = db.insert_invoice(conn, _source("detail.pdf"), _fields(), decision="EXCEPTION")
    db.insert_exceptions(conn, invoice_id, [C.PRICE_VARIANCE], explanation="price is off",
                          suggested_action="confirm with vendor")
    row = db.get_invoice(conn, invoice_id)
    assert row["source_file"] == _source("detail.pdf")
    exceptions = db.get_exceptions(conn, invoice_id)
    assert len(exceptions) == 1
    assert exceptions[0]["code"] == C.PRICE_VARIANCE
    assert exceptions[0]["explanation"] == "price is off"


def test_get_invoice_returns_none_for_unknown_id(conn):
    assert db.get_invoice(conn, 999_999_999) is None


def test_try_consume_daily_quota_increments_and_respects_limit(conn):
    """Restores the real counter afterward -- this table has no
    SOURCE_PREFIX to sweep up by, and it's the same counter the live app
    uses, so a test must not permanently inflate it."""
    with conn.cursor() as cur:
        cur.execute("SELECT count FROM ap.api_usage WHERE day = CURRENT_DATE")
        row = cur.fetchone()
    before = row[0] if row else 0
    try:
        allowed, count = db.try_consume_daily_quota(conn, daily_limit=before + 5)
        assert count == before + 1
        assert allowed is True
        allowed_tight, count2 = db.try_consume_daily_quota(conn, daily_limit=before + 1)
        assert count2 == before + 2
        assert allowed_tight is False   # before+2 > before+1
    finally:
        with conn.cursor() as cur:
            cur.execute("UPDATE ap.api_usage SET count = %s WHERE day = CURRENT_DATE", (before,))
        conn.commit()


def test_audit_log_is_append_only_by_source_inspection():
    """Structural guarantee promised in db.py's own module docstring: grep
    the source for any write path to ap.audit_log other than the single
    INSERT in log_audit(). This makes the append-only property something
    that breaks a test (not just a convention) if someone adds an UPDATE or
    DELETE against audit_log later.

    The module docstring itself quotes those exact phrases ("UPDATE
    ap.audit_log", "DELETE FROM ap.audit_log") as prose explaining this very
    guarantee -- a naive full-file substring search self-triggers on its own
    documentation, so the docstring is stripped before scanning."""
    text = (ROOT / "src" / "invoice_agent" / "db.py").read_text()
    first = text.find('"""')
    second = text.find('"""', first + 3)
    code_only = (text[:first] + text[second + 3:]).upper()
    assert "UPDATE AP.AUDIT_LOG" not in code_only
    assert "DELETE FROM AP.AUDIT_LOG" not in code_only
    assert code_only.count("INSERT INTO AP.AUDIT_LOG") == 1
