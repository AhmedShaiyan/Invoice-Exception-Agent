"""Exception queue: evidence side-by-side (invoice vs. PO vs. goods
receipt), the investigation agent's findings and tool trail, and the one
place in this app a human can approve or reject an invoice. Nothing else
in the codebase has that capability -- see agent.py's module docstring
and docs/design_decisions.md for why that's a hard boundary, not a
convention.
"""
import json
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from invoice_agent import codes as C  # noqa: E402
from invoice_agent import db  # noqa: E402
from invoice_agent.app_auth import require_auth  # noqa: E402
from invoice_agent.ledger import Ledger  # noqa: E402
from invoice_agent.sap_data import SapData  # noqa: E402

st.set_page_config(page_title="Exception Queue", page_icon="📄", layout="wide")
require_auth()


@st.cache_resource
def _load_sap():
    sap_dir = ROOT / "data" / "sap"
    return SapData.from_csvs(sap_dir), Ledger.from_csvs(sap_dir)


sap, ledger = _load_sap()
conn = db.connect()

st.title("Exception Queue")

pending = [r for r in db.list_invoices(conn, decision="EXCEPTION")]
reviewed_ids = {r["id"] for r in db.list_invoices(conn, decision="APPROVED")} | \
               {r["id"] for r in db.list_invoices(conn, decision="REJECTED")}
pending = [r for r in pending if r["id"] not in reviewed_ids]

st.caption(f"{len(pending)} invoice(s) awaiting review.")

if not pending:
    st.success("Nothing waiting for review.")
    conn.close()
    st.stop()

labels = [f"#{r['id']} -- {r['source_file']} -- {r['vendor_name'] or '(unknown vendor)'} -- "
          f"{r['total'] or '?'} {r['currency'] or ''}" for r in pending]
selected = st.selectbox("Select an invoice", options=range(len(pending)), format_func=lambda i: labels[i])
invoice_id = pending[selected]["id"]

invoice = db.get_invoice(conn, invoice_id)
exceptions = db.get_exceptions(conn, invoice_id)
trail = db.get_audit_trail(conn, invoice_id)
extracted = invoice.get("extracted_json") or {}

left, right = st.columns(2)

with left:
    st.subheader("Invoice (as extracted)")
    st.write(f"**Vendor:** {invoice.get('vendor_name')}")
    st.write(f"**Invoice #:** {invoice.get('invoice_number')}  |  **Date:** {invoice.get('invoice_date')}")
    st.write(f"**PO ref:** {invoice.get('po_number')}  |  **GST reg:** {invoice.get('vendor_gst_reg_no')}")
    st.write(f"**Subtotal:** {invoice.get('subtotal')}  |  **GST:** {invoice.get('gst_rate')} "
             f"({invoice.get('gst_amount')})  |  **Total:** {invoice.get('total')} {invoice.get('currency')}")
    lines = extracted.get("lines", [])
    if lines:
        st.dataframe([{"description": ln.get("description"), "qty": ln.get("quantity"),
                        "unit": ln.get("unit"), "unit_price": ln.get("unit_price"),
                        "amount": ln.get("amount")} for ln in lines], use_container_width=True, hide_index=True)

with right:
    st.subheader("PO evidence (what SAP says)")
    po_numbers = sorted({p for p in (invoice.get("po_number") or "").split("/") if p})
    if not po_numbers:
        st.info("No PO referenced on this invoice.")
    for ebeln in po_numbers:
        head = sap.po_header.get(ebeln)
        if head is None:
            st.warning(f"PO {ebeln}: not found in SAP master data.")
            continue
        st.write(f"**PO {ebeln}** -- vendor {sap.vendors.get(head['lifnr'], {}).get('name1', '?')}, "
                 f"currency {head['waers']}, dated {head['bedat']}")
        po_lines = sap.po_lines_by_po.get(ebeln, [])
        rows = []
        for ln in po_lines:
            remaining = ledger.remaining_qty(ebeln, ln["ebelp"])
            rows.append({"item": ln["ebelp"], "material": ln["matnr"], "description": ln["txz01"],
                          "order_qty": ln["menge"], "unit": ln["meins"], "net_price": ln["netpr"],
                          "price_unit": ln["peinh"], "remaining_invoiceable": str(remaining)})
        st.dataframe(rows, use_container_width=True, hide_index=True)

st.divider()
st.subheader("Exceptions and agent findings")
for exc in exceptions:
    sev_color = {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟡"}.get(exc["severity"], "⚪")
    with st.expander(f"{sev_color} {exc['code']} ({exc['severity']}) -- {C.DESCRIPTIONS.get(exc['code'], '')}",
                      expanded=True):
        detail = exc.get("detail")
        if detail:
            st.json(detail)
        if exc.get("explanation"):
            st.markdown(f"**Agent explanation:** {exc['explanation']}")
            st.markdown(f"**Suggested action:** {exc.get('suggested_action') or '(none)'}")
        else:
            st.caption("Not investigated (see docs/decisions_log.md section 6 for why this can happen -- "
                       "a validator correction after the fact, or investigation hasn't run yet).")

investigate_entries = [e for e in trail if e["action"] == "investigate"]
if investigate_entries:
    with st.expander("Investigation tool-call trail"):
        tool_calls = investigate_entries[-1]["payload"].get("tool_calls", [])
        if not tool_calls:
            st.caption("No tools were called -- the agent answered from the invoice fields alone.")
        for call in tool_calls:
            st.markdown(f"**{call['tool_name']}**`({json.dumps(call['input'])})`")
            st.json(call["output"])

st.divider()
st.subheader("Review decision")
reason = st.text_area("Reason (recorded in the audit trail)")
col_a, col_r = st.columns(2)
reviewer = st.session_state.get("reviewer_name", "demo-reviewer")
if col_a.button("✅ Approve", use_container_width=True):
    if not reason.strip():
        st.error("Enter a reason before recording a decision.")
    else:
        db.record_human_decision(conn, invoice_id, "APPROVED", reviewer=reviewer, reason=reason)
        st.success("Recorded. Reloading queue...")
        st.rerun()
if col_r.button("❌ Reject", use_container_width=True):
    if not reason.strip():
        st.error("Enter a reason before recording a decision.")
    else:
        db.record_human_decision(conn, invoice_id, "REJECTED", reviewer=reviewer, reason=reason)
        st.success("Recorded. Reloading queue...")
        st.rerun()

conn.close()
