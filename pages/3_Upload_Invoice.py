"""Upload your own invoice and watch the real pipeline run on it --
extraction, three-way match, GST checks, and (if it's flagged) the
investigation agent. Matched against this demo's *mock* SAP data
(18 vendors, ~400 purchase orders -- see scripts/generate_mock_sap.py),
not any real company's purchasing system, so an upload that doesn't
reference one of the demo vendors/POs will correctly come back
VENDOR_NOT_FOUND or PO_NOT_FOUND -- that's the system working, not a bug.

Makes real Claude API calls, so this page is rate-limited by a shared
daily quota (invoice_agent.db.try_consume_daily_quota) across every
visitor, not a per-session one -- see app_auth.py and
docs/decisions_log.md section 7.
"""
import sys
import tempfile
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from invoice_agent import db  # noqa: E402
from invoice_agent.agent import Investigator  # noqa: E402
from invoice_agent.app_auth import daily_limit, require_auth  # noqa: E402
from invoice_agent.idempotency import IdempotencyStore  # noqa: E402
from invoice_agent.ledger import Ledger  # noqa: E402
from invoice_agent.pipeline import process_invoice  # noqa: E402
from invoice_agent.sap_data import SapData  # noqa: E402
from invoice_agent.validate import Validator  # noqa: E402

st.set_page_config(page_title="Upload Invoice", page_icon="📄", layout="wide")
require_auth()

SAP_DIR = ROOT / "data" / "sap"


@st.cache_resource
def _load_static_sap_data():
    return SapData.from_csvs(SAP_DIR)


st.title("Upload Your Own Invoice")
st.warning(
    "This demo matches every upload against **mock** SAP data seeded for this project "
    "(18 fictional Singapore vendors, ~400 purchase orders) -- not any real company's "
    "purchasing system. An invoice from a real vendor will correctly come back "
    "VENDOR_NOT_FOUND or PO_NOT_FOUND; that's the three-way match working as designed, "
    "not a bug. See scripts/generate_mock_sap.py for the full mock vendor/PO list.",
    icon="⚠️",
)

conn = db.connect()
limit = daily_limit()
with conn.cursor() as cur:
    cur.execute("SELECT count FROM ap.api_usage WHERE day = CURRENT_DATE")
    row = cur.fetchone()
used_today = row[0] if row else 0
st.caption(f"Shared daily quota: {used_today}/{limit} uploads processed today across all visitors.")

uploaded = st.file_uploader("Invoice PDF or e-invoice JSON", type=["pdf", "json"])

if uploaded is not None:
    if used_today >= limit:
        st.error(
            f"Today's shared quota of {limit} uploads has been reached (demo cost control -- "
            "this calls the real Claude API). Please try again tomorrow."
        )
    elif st.button("Process this invoice"):
        allowed, count = db.try_consume_daily_quota(conn, limit)
        if not allowed:
            st.error(f"Quota reached mid-request ({count}/{limit}) -- another visitor got there first. Try again tomorrow.")
        else:
            suffix = ".einvoice.json" if uploaded.name.lower().endswith(".json") else ".pdf"
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / f"upload{suffix}"
                path.write_bytes(uploaded.getvalue())

                # Fresh Ledger per upload, deliberately not cached: a public
                # demo must not let one visitor's upload leave quantity
                # "consumed" that silently changes another visitor's result.
                # SapData (static master data) is still cached -- it never
                # changes.
                validator = Validator(_load_static_sap_data(), Ledger.from_csvs(SAP_DIR))
                investigator = Investigator(_load_static_sap_data(), Ledger.from_csvs(SAP_DIR), SAP_DIR)
                idem = IdempotencyStore(Path(tmp) / "idem.sqlite3")

                with st.spinner("Extracting fields and validating..."):
                    result = process_invoice(path, validator, investigator, idem, conn=conn)

            st.divider()
            if result.decision == "AUTO_APPROVE":
                st.success(f"**AUTO_APPROVE** -- passed every check (invoice #{result.invoice_id}).")
            else:
                st.error(f"**EXCEPTION** -- {', '.join(result.exception_codes)} (invoice #{result.invoice_id})")
            if result.extraction_status != "ok":
                st.caption(f"Extraction status: {result.extraction_status}")

            trail = db.get_audit_trail(conn, result.invoice_id)
            investigate = next((e for e in trail if e["action"] == "investigate"), None)
            if investigate:
                st.subheader("Agent investigation")
                st.markdown(f"**Explanation:** {investigate['payload'].get('explanation')}")
                st.markdown(f"**Suggested action:** {investigate['payload'].get('suggested_action')}")

            invoice_row = db.get_invoice(conn, result.invoice_id)
            with st.expander("Extracted fields"):
                st.json(invoice_row.get("extracted_json") or {})

            st.caption("This upload is now in the database and will appear in the Exception Queue "
                       "and Audit Trail pages like any other invoice.")

conn.close()
