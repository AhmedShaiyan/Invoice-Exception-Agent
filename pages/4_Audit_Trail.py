"""Audit trail: the full append-only history for any invoice, not just
the ones with exceptions. ap.audit_log only ever gets INSERTed into, by
exactly one function (invoice_agent.db.log_audit) -- this page is a
direct, unfiltered read of what actually happened, in order, for every
invoice this system has ever touched.
"""
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from invoice_agent import db  # noqa: E402
from invoice_agent import present  # noqa: E402

st.set_page_config(page_title="Audit Trail", page_icon="🧾", layout="wide")

conn = db.connect()

st.title("Audit Trail")
st.caption("Every pipeline step and human decision, in the order it actually happened. "
           "Nothing here is ever edited or deleted.")

all_invoices = db.list_invoices(conn, limit=500)
if not all_invoices:
    st.info("No invoices processed yet.")
    conn.close()
    st.stop()

labels = [f"{r['vendor_name'] or '(unknown)'} -- {r['invoice_number'] or r['source_file']} -- {r['decision']}"
          for r in all_invoices]
selected = st.selectbox("Invoice", options=range(len(all_invoices)), format_func=lambda i: labels[i])
invoice_id = all_invoices[selected]["id"]

trail = db.get_audit_trail(conn, invoice_id)
if not trail:
    st.warning("No audit entries for this invoice.")
else:
    icons = {"extract": "📥", "validate": "✅", "investigate": "🔎", "revalidate": "🔁",
             "approved": "✅", "rejected": "❌", "held": "⏸️",
             "draft_vendor_query": "✉️", "vendor_query_sent": "📤"}
    for entry in trail:
        icon = icons.get(entry["action"], "•")
        header = f"{icon} {entry['created_at']} -- {entry['actor']} -- {entry['action']}"
        if entry["model"]:
            header += f" ({entry['model']})"
        with st.expander(header):
            st.markdown(present.format_audit_entry(entry["action"], entry["payload"]))
            if entry["action"] == "investigate":
                for call in (entry["payload"] or {}).get("tool_calls", []):
                    st.markdown(f"- {present.format_tool_call(call)}")

conn.close()
