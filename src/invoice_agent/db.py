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


def insert_invoice(conn, source_file: str, fields: dict, decision: str) -> int:
    """fields is an extraction output dict (possibly with some keys missing
    if extraction failed -- insert what's available, NULL the rest)."""
    fields = fields or {}
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO ap.invoices
               (source_file, vendor_name, vendor_gst_reg_no, invoice_number, invoice_date,
                po_number, currency, subtotal, gst_rate, gst_amount, total, extracted_json, decision)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (source_file, fields.get("vendor_name"), fields.get("vendor_gst_reg_no"),
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
    """decision must be 'APPROVED' or 'REJECTED' -- enforced by the schema's
    CHECK constraint too, this is just a clearer failure point."""
    if decision not in ("APPROVED", "REJECTED"):
        raise ValueError(f"decision must be APPROVED or REJECTED, got {decision!r}")
    update_decision(conn, invoice_id, decision)
    log_audit(conn, invoice_id, f"user:{reviewer}", decision.lower(), payload={"reason": reason})


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
