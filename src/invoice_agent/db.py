"""Postgres persistence: ap.invoices, ap.exceptions, ap.audit_log.

ap.audit_log is append-only by construction, not just convention: this
module contains exactly one function that writes to it (`log_audit`), and
that function only ever INSERTs. Grep this file for "UPDATE ap.audit_log"
or "DELETE FROM ap.audit_log" -- there isn't one, and tests/test_db.py
checks that by searching the source directly, so the guarantee can't
silently erode as the module grows.

ap.invoices.decision is the one piece of genuinely mutable state: the
pipeline sets it to AUTO_APPROVE/EXCEPTION, and a human review can later
change it to APPROVED/REJECTED. That's an ordinary "current state" update,
not a violation of the audit principle -- every transition is independently
recorded in audit_log via log_audit() in the same call, so the full history
survives even though the "current state" table doesn't preserve it itself.
"""
import json
import os
from decimal import Decimal

import psycopg
from dotenv import load_dotenv

from . import codes as C

load_dotenv()


def connect():
    return psycopg.connect(os.environ["DATABASE_URL"])


def _money(d):
    return str(d) if d is not None else None


def insert_invoice(conn, source_file: str, fields: dict, decision: str, lifnr: str = None) -> int:
    """fields is an extraction output dict (possibly with some keys missing
    if extraction failed -- insert what's available, NULL the rest). lifnr
    is the validator's resolved vendor match (ValidationResult.matched_vendor),
    None when extraction failed or the vendor couldn't be resolved -- joined
    against sap.lfa1 later (e.g. for payment terms) by callers that need it."""
    fields = fields or {}
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO ap.invoices
               (source_file, vendor_name, vendor_gst_reg_no, lifnr, invoice_number, invoice_date,
                po_number, currency, subtotal, gst_rate, gst_amount, total, extracted_json, decision)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (source_file, fields.get("vendor_name"), fields.get("vendor_gst_reg_no"), lifnr,
             fields.get("invoice_number"), fields.get("invoice_date") or None,
             fields.get("po_number"), fields.get("currency"), _money(fields.get("subtotal")),
             fields.get("gst_rate"), _money(fields.get("gst_amount")), _money(fields.get("total")),
             json.dumps(fields), decision),
        )
        invoice_id = cur.fetchone()[0]
    conn.commit()
    return invoice_id


def insert_exceptions(conn, invoice_id: int, exception_codes: list, detail_by_code: dict = None,
                       explanation: str = None, suggested_action: str = None) -> None:
    detail_by_code = detail_by_code or {}
    with conn.cursor() as cur:
        for code in exception_codes:
            cur.execute(
                """INSERT INTO ap.exceptions
                   (invoice_id, code, severity, detail, explanation, suggested_action)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (invoice_id, code, C.SEVERITY.get(code, "MEDIUM"),
                 json.dumps(detail_by_code.get(code, {})), explanation, suggested_action),
            )
    conn.commit()


def update_decision(conn, invoice_id: int, decision: str) -> None:
    """The one legitimate update path -- see module docstring. Always
    paired with a log_audit() call recording the same transition."""
    with conn.cursor() as cur:
        cur.execute("UPDATE ap.invoices SET decision = %s WHERE id = %s", (decision, invoice_id))
    conn.commit()


def log_audit(conn, invoice_id, actor: str, action: str, payload: dict = None, model: str = None) -> None:
    """The only write path to ap.audit_log. INSERT only -- see module docstring."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ap.audit_log (invoice_id, actor, action, payload, model) VALUES (%s,%s,%s,%s,%s)",
            (invoice_id, actor, action, json.dumps(payload or {}, default=str), model),
        )
    conn.commit()


def record_human_decision(conn, invoice_id: int, decision: str, reviewer: str, reason: str) -> None:
    """decision must be 'APPROVED', 'REJECTED', or 'HELD' -- enforced by the
    schema's CHECK constraint too, this is just a clearer failure point."""
    if decision not in ("APPROVED", "REJECTED", "HELD"):
        raise ValueError(f"decision must be APPROVED, REJECTED, or HELD, got {decision!r}")
    update_decision(conn, invoice_id, decision)
    log_audit(conn, invoice_id, f"user:{reviewer}", decision.lower(), payload={"reason": reason})


def current_investigate_entry(trail: list):
    """The most recent 'investigate' entry from get_audit_trail's output,
    but only if nothing invalidated it since. A 'revalidate' entry (a
    manual re-validation after a deterministic code fix -- see
    decisions_log_full.md section 6) logged after the investigation means that
    explanation analyzed a code the invoice no longer carries; showing or
    scoring it as current would check a claim against the wrong problem.
    The audit log itself is never edited (append-only by design) -- this
    just stops a stale entry from being treated as current. Shared by
    scripts/evaluate.py's groundedness scoring and the Invoice Review
    page's "How this was checked" section, so the two can't drift."""
    investigate_entries = [e for e in trail if e["action"] == "investigate"]
    if not investigate_entries:
        return None
    latest_investigate = investigate_entries[-1]
    revalidate_entries = [e for e in trail if e["action"] == "revalidate"]
    if revalidate_entries and revalidate_entries[-1]["created_at"] > latest_investigate["created_at"]:
        return None
    return latest_investigate


def get_audit_trail(conn, invoice_id: int) -> list:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, actor, action, payload, model, created_at FROM ap.audit_log "
            "WHERE invoice_id = %s ORDER BY created_at, id", (invoice_id,))
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ======================================================== read queries (app)
def _rows_as_dicts(cur) -> list:
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def dashboard_stats(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT decision, count(*) FROM ap.invoices GROUP BY decision")
        by_decision = dict(cur.fetchall())
        cur.execute("SELECT count(*) FROM ap.invoices")
        total = cur.fetchone()[0]
    return {"total": total, "by_decision": by_decision}


def list_invoices(conn, decision: str = None, limit: int = 300) -> list:
    """Newest first. `decision` filters to one value (e.g. 'EXCEPTION');
    omit for everything."""
    with conn.cursor() as cur:
        if decision:
            cur.execute(
                "SELECT id, source_file, vendor_name, invoice_number, invoice_date, total, "
                "currency, decision, created_at FROM ap.invoices WHERE decision = %s "
                "ORDER BY created_at DESC, id DESC LIMIT %s", (decision, limit))
        else:
            cur.execute(
                "SELECT id, source_file, vendor_name, invoice_number, invoice_date, total, "
                "currency, decision, created_at FROM ap.invoices "
                "ORDER BY created_at DESC, id DESC LIMIT %s", (limit,))
        return _rows_as_dicts(cur)


def get_invoice(conn, invoice_id: int) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM ap.invoices WHERE id = %s", (invoice_id,))
        rows = _rows_as_dicts(cur)
    return rows[0] if rows else None


def get_exceptions(conn, invoice_id: int) -> list:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, code, severity, detail, explanation, suggested_action FROM ap.exceptions "
            "WHERE invoice_id = %s ORDER BY severity DESC, code", (invoice_id,))
        return _rows_as_dicts(cur)


def list_open_exceptions(conn) -> list:
    """One row per invoice still sitting in EXCEPTION (not yet approved,
    rejected, or held), with the vendor's payment terms (sap.lfa1.zterm)
    joined in so the caller can compute a due date, and every exception
    code raised aggregated into one array -- everything the Worklist page
    needs in a single query rather than N+1 round trips."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT i.id, i.source_file, i.vendor_name, i.lifnr, i.invoice_number,
                      i.invoice_date, i.total, i.currency, v.zterm,
                      COALESCE(array_agg(DISTINCT e.code) FILTER (WHERE e.code IS NOT NULL), '{}') AS codes
               FROM ap.invoices i
               LEFT JOIN sap.lfa1 v ON i.lifnr = v.lifnr
               LEFT JOIN ap.exceptions e ON e.invoice_id = i.id
               WHERE i.decision = 'EXCEPTION'
               GROUP BY i.id, v.zterm
               ORDER BY i.id""")
        return _rows_as_dicts(cur)


def vendor_scorecard(conn) -> list:
    """Vendors ranked by how much exception volume/value they cause --
    across every invoice ever decided EXCEPTION, not just currently-open
    ones, since a vendor's track record doesn't reset when a human clears
    today's queue.

    Grouped by lifnr (the resolved vendor), not the raw extracted
    vendor_name text -- different invoices from the same real vendor can
    have different case/whitespace in how their name was printed and
    read (e.g. "Redwood Overseas Equipment Inc" vs "REDWOOD OVERSEAS
    EQUIPMENT INC"), which split one vendor into multiple scorecard rows
    when grouped by the raw text. Falls back to vendor_name for the rare
    unresolved case (VENDOR_NOT_FOUND, no lifnr to group by) -- there's
    no canonical identity to group those on."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT COALESCE(v.name1, MAX(i.vendor_name)) AS vendor_name,
                      count(*) AS exception_count,
                      COALESCE(sum(i.total), 0) AS exception_value
               FROM ap.invoices i
               LEFT JOIN sap.lfa1 v ON i.lifnr = v.lifnr
               WHERE i.decision IN ('EXCEPTION','APPROVED','REJECTED','HELD')
                 AND EXISTS (SELECT 1 FROM ap.exceptions e WHERE e.invoice_id = i.id)
               GROUP BY COALESCE(i.lifnr, i.vendor_name), v.name1
               ORDER BY exception_count DESC, exception_value DESC
               LIMIT 10""")
        return _rows_as_dicts(cur)


def exceptions_by_code(conn) -> list:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT code, count(*) AS n FROM ap.exceptions
               GROUP BY code ORDER BY n DESC""")
        return _rows_as_dicts(cur)


def value_on_hold_by_day(conn) -> list:
    """Sum of invoice total for currently-open exceptions, grouped by the
    day they were processed. With only one batch eval run behind this demo
    (not daily operations), expect this to look like a single spike rather
    than a smooth trend -- the query is correct either way and will show a
    real multi-day trend once Upload traffic accumulates across days."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT created_at::date AS day, COALESCE(sum(total), 0) AS value
               FROM ap.invoices
               WHERE decision = 'EXCEPTION'
               GROUP BY day ORDER BY day""")
        return _rows_as_dicts(cur)


def try_consume_daily_quota(conn, daily_limit: int) -> tuple:
    """Atomically increments today's shared usage counter (one row per
    calendar day, UPSERT with RETURNING -- Postgres's row lock makes this
    safe under concurrent requests, no separate locking needed) and
    reports whether the caller is still within budget. Shared across every
    visitor on purpose: a public deployment with no per-user accounts
    needs one cap everyone draws from, not a per-session counter a
    visitor could reset by reloading the page. Returns (allowed, count).

    Uses Postgres's CURRENT_DATE, not Python's date.today(), so the write
    path agrees with every read path (the Upload page's usage display,
    this function's own callers) on what day it is. date.today() reads the
    app server's local clock; CURRENT_DATE reads the DB server's -- mixing
    the two let a row get written under one day and read back under
    another whenever the two clocks' local dates disagreed (e.g. near
    midnight UTC when the app server runs in a different timezone)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ap.api_usage (day, count) VALUES (CURRENT_DATE, 1) "
            "ON CONFLICT (day) DO UPDATE SET count = ap.api_usage.count + 1 "
            "RETURNING count")
        count = cur.fetchone()[0]
    conn.commit()
    return count <= daily_limit, count
