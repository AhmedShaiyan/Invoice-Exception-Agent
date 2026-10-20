"""Streamlit app entry point: Worklist. Run with `streamlit run app.py`.

This page plus `pages/` are the human-facing side of the architecture
described in .claude/CLAUDE.md: deterministic validation and a bounded,
read-only agent decide and investigate; nothing in this app (or anywhere
in the codebase) lets the system approve or reject its own work -- those
actions exist only on the Invoice Review page, and it's a human clicking
them.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from invoice_agent import codes as C  # noqa: E402
from invoice_agent import db  # noqa: E402

st.set_page_config(page_title="AP Exception Worklist", page_icon="📋", layout="wide")

st.title("AP Exception Worklist")

conn = db.connect()
stats = db.dashboard_stats(conn)
total = stats["total"]
auto_approved = stats["by_decision"].get("AUTO_APPROVE", 0)
straight_through_rate = (auto_approved / total * 100) if total else 0.0

rows = db.list_open_exceptions(conn)
conn.close()

today = date.today()


def _due_date(row):
    if not row["zterm"] or not row["invoice_date"]:
        return None
    try:
        days = int(row["zterm"][2:])
    except ValueError:
        return None
    return row["invoice_date"] + timedelta(days=days)


for r in rows:
    r["due_date"] = _due_date(r)
    r["days_to_due"] = (r["due_date"] - today).days if r["due_date"] else None
    r["issues"] = ", ".join(C.PLAIN_LABELS.get(code, code) for code in r["codes"])

value_on_hold = sum((r["total"] or 0) for r in rows)
due_this_week = sum(1 for r in rows if r["days_to_due"] is not None and 0 <= r["days_to_due"] <= 7)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Open exceptions", len(rows))
col2.metric("Value on hold", f"SGD {value_on_hold:,.2f}")
col3.metric("Due within 7 days", due_this_week)
col4.metric("Straight-through rate", f"{straight_through_rate:.1f}%")

st.divider()

if not rows:
    st.success("No open exceptions.")
    st.stop()

all_issues = sorted({code for r in rows for code in r["codes"]})
all_vendors = sorted({r["vendor_name"] for r in rows if r["vendor_name"]})

fcol1, fcol2 = st.columns(2)
issue_filter = fcol1.multiselect(
    "Filter by issue", options=all_issues, format_func=lambda c: C.PLAIN_LABELS.get(c, c))
vendor_filter = fcol2.multiselect("Filter by vendor", options=all_vendors)

filtered = rows
if issue_filter:
    filtered = [r for r in filtered if any(c in issue_filter for c in r["codes"])]
if vendor_filter:
    filtered = [r for r in filtered if r["vendor_name"] in vendor_filter]

filtered.sort(key=lambda r: (r["days_to_due"] is None, r["days_to_due"] if r["days_to_due"] is not None else 0))

st.caption(f"{len(filtered)} invoice(s)")

table_rows = [{
    "id": r["id"],
    "Vendor": r["vendor_name"] or "(unknown)",
    "Invoice #": r["invoice_number"] or r["source_file"],
    "Amount": f"{r['currency'] or ''} {r['total']:,.2f}" if r["total"] is not None else "?",
    "Issue(s)": r["issues"] or "(none)",
    "Due date": r["due_date"].isoformat() if r["due_date"] else "unknown",
    # str(), not the bare int: a column mixing int and the "unknown"
    # string for rows with no resolvable due date broke Arrow
    # serialization (Streamlit auto-recovered with a type-coercion
    # warning, but a consistently-typed column is the actual fix). Rows
    # are already sorted in Python above, so a string column here costs
    # nothing -- display order doesn't depend on this column's dtype.
    "Days to due": str(r["days_to_due"]) if r["days_to_due"] is not None else "unknown",
} for r in filtered]

event = st.dataframe(
    table_rows, use_container_width=True, hide_index=True,
    column_order=["Vendor", "Invoice #", "Amount", "Issue(s)", "Due date", "Days to due"],
    on_select="rerun", selection_mode="single-row",
)

if event.selection.rows:
    selected = filtered[event.selection.rows[0]]
    st.session_state["selected_invoice_id"] = selected["id"]
    st.switch_page("pages/1_Invoice_Review.py")
