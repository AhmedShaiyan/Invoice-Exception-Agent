"""Streamlit app entry point: Dashboard. Run with `streamlit run app.py`
from the `invoice-agent/` directory.

This page plus `pages/` are the human-facing side of the architecture
described in CLAUDE.md: deterministic validation and a bounded,
read-only agent decide and investigate; nothing in this app (or anywhere
in the codebase) lets the system approve or reject its own work -- that
button exists only on the Exception Queue page, and it's a human
clicking it.
"""
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from invoice_agent import db  # noqa: E402
from invoice_agent.app_auth import require_auth  # noqa: E402

st.set_page_config(page_title="Invoice Exception Agent", page_icon="📄", layout="wide")
require_auth()

st.title("Invoice Exception Agent")
st.caption(
    "Accounts-payable three-way match (invoice vs. PO vs. goods receipt) + Singapore GST checks. "
    "Deterministic Python decides; Claude only extracts fields and explains exceptions -- "
    "see CLAUDE.md for the full architecture."
)

conn = db.connect()
stats = db.dashboard_stats(conn)
by_decision = stats["by_decision"]
total = stats["total"]
auto_approved = by_decision.get("AUTO_APPROVE", 0)
exceptions = by_decision.get("EXCEPTION", 0)
approved = by_decision.get("APPROVED", 0)
rejected = by_decision.get("REJECTED", 0)
reviewed = approved + rejected
pending = exceptions - reviewed

st.info(
    f"These {total} invoices are the labelled evaluation set this project is scored "
    f"against (not live traffic -- that only grows if someone uses **Upload Invoice** "
    f"below). **{auto_approved}** passed every check and were auto-approved with no "
    f"human involved. **{exceptions}** failed at least one check and were routed to a "
    f"person instead of being guessed at -- of those, **{pending}** are still "
    f"unreviewed and **{reviewed}** already have a human decision on record.",
    icon="ℹ️",
)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Invoices processed", total)
col2.metric("Auto-approved", auto_approved, help="Passed three-way match + GST checks with no exceptions.")
col3.metric("Exceptions", exceptions, help="Routed to human review -- see the Exception Queue page.")
col4.metric("Reviewed", reviewed, help=f"{approved} approved, {rejected} rejected by a human.")

if pending > 0:
    st.warning(f"**{pending} invoice(s) are waiting for a decision.**")
    st.page_link("pages/1_Exception_Queue.py", label="Go to Exception Queue →", icon="📋")
else:
    st.success("Every exception has a human decision on record.")
    st.page_link("pages/2_Audit_Trail.py", label="Browse the audit trail →", icon="🧾")

st.page_link("pages/3_Upload_Invoice.py", label="Try it on your own invoice →", icon="📄")

st.divider()

eval_report_path = ROOT / "docs" / "eval_report.md"
if eval_report_path.exists():
    st.subheader("Evaluation results")
    st.caption(
        "Scored against hand-labelled ground truth on the full 217-invoice dataset -- "
        "see docs/decisions_log.md section 6 for how this number was produced and verified."
    )
    with st.expander("Full evaluation report", expanded=False):
        st.markdown(eval_report_path.read_text())
else:
    st.info("No evaluation report found yet -- run `python scripts/evaluate.py`.")

conn.close()
