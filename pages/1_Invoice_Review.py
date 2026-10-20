"""Invoice review: the original document, a line-by-line match against the
PO/goods-receipt record, the investigation agent's findings, and the only
place in this app a human can approve, reject, hold, or ask a vendor for
a correction. Nothing else in the codebase has that capability.
"""
import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pypdfium2 as pdfium
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from invoice_agent import codes as C  # noqa: E402
from invoice_agent import db  # noqa: E402
from invoice_agent import present  # noqa: E402
from invoice_agent.agent import draft_vendor_query  # noqa: E402
from invoice_agent.ledger import Ledger  # noqa: E402
from invoice_agent.quota import daily_limit  # noqa: E402
from invoice_agent.sap_data import SapData  # noqa: E402
from invoice_agent.validate import PRICE_TOLERANCE, PRICE_TOLERANCE_FLOOR, Validator  # noqa: E402

st.set_page_config(page_title="Invoice Review", page_icon="🧾", layout="wide")

SAP_DIR = ROOT / "data" / "sap"
INVOICES_DIR = ROOT / "data" / "invoices"


@st.cache_resource
def _load_sap():
    return SapData.from_csvs(SAP_DIR), Ledger.from_csvs(SAP_DIR)


sap, ledger = _load_sap()
validator = Validator(sap, ledger)

conn = db.connect()
open_rows = db.list_open_exceptions(conn)

st.title("Invoice Review")

if not open_rows:
    st.success("No open exceptions to review.")
    conn.close()
    st.stop()

ids = [r["id"] for r in open_rows]
labels = {r["id"]: f"{r['vendor_name'] or '(unknown)'} -- {r['invoice_number'] or r['source_file']}"
          for r in open_rows}
default_id = st.session_state.get("selected_invoice_id")
default_idx = ids.index(default_id) if default_id in ids else 0
selected_id = st.selectbox("Invoice", options=ids, index=default_idx, format_func=lambda i: labels[i])
st.session_state["selected_invoice_id"] = selected_id

invoice = db.get_invoice(conn, selected_id)
exceptions = db.get_exceptions(conn, selected_id)
trail = db.get_audit_trail(conn, selected_id)
extracted = invoice.get("extracted_json") or {}
codes_present = {e["code"] for e in exceptions}

left, right = st.columns([1, 1])

with left:
    st.subheader("Original document")
    pdf_path = INVOICES_DIR / invoice["source_file"]
    if pdf_path.suffix.lower() == ".pdf":
        try:
            pdf = pdfium.PdfDocument(str(pdf_path))
            bitmap = pdf[0].render(scale=150 / 72)
            st.image(bitmap.to_pil(), use_container_width=True)
        except Exception as e:
            st.warning(f"Couldn't render the PDF: {e}")
    else:
        st.info("This was submitted as a structured e-invoice (no PDF page to render).")
        st.json(extracted, expanded=False)

with right:
    st.subheader("Findings")
    investigate_entry = db.current_investigate_entry(trail)
    if investigate_entry:
        payload = investigate_entry["payload"]
        st.markdown(payload.get("explanation", "(no explanation recorded)"))
        st.markdown(f"**Recommended action:** {payload.get('suggested_action', '(none)')}")
    else:
        for exc in exceptions:
            for sentence in present.format_detail_sentences(exc["code"], exc.get("detail")):
                st.markdown(f"- **{C.PLAIN_LABELS.get(exc['code'], exc['code'])}:** {sentence}")

st.divider()
st.subheader("Line-by-line match")

match_results = validator.match_lines(extracted)
lines = extracted.get("lines", [])
table_rows = []
style_rows = []
for ln, lr in zip(lines, match_results):
    row = {"Description": ln.get("description"), "Invoiced qty": ln.get("quantity"),
           "Invoiced unit": ln.get("unit"), "Invoiced price": ln.get("unit_price")}
    cell_flags = {k: False for k in row}
    if lr.matched:
        ekpo = lr.ekpo
        row["PO qty"] = ekpo.get("menge")
        row["PO unit"] = ekpo.get("meins")
        row["PO price"] = ekpo.get("netpr")
        received = ledger.gr_qty.get((ekpo["ebeln"], ekpo["ebelp"]), Decimal("0"))
        row["Received qty"] = str(received)

        if "QTY_EXCEEDS_GR" in codes_present:
            try:
                if Decimal(str(ln.get("quantity"))) > received:
                    cell_flags["Invoiced qty"] = True
            except Exception:
                pass
        if codes_present & {"PRICE_VARIANCE", "PRICE_UNIT_MISMATCH"}:
            try:
                true_price = Decimal(ekpo["netpr"]) / Decimal(ekpo["peinh"])
                inv_price = Decimal(str(ln.get("unit_price")))
                tolerance = max(true_price * PRICE_TOLERANCE, PRICE_TOLERANCE_FLOOR)
                if abs(inv_price - true_price) > tolerance:
                    cell_flags["Invoiced price"] = True
            except Exception:
                pass
        if "UOM_MISMATCH" in codes_present and ln.get("unit") and ekpo.get("meins"):
            if ln["unit"].upper() != ekpo["meins"].upper():
                cell_flags["Invoiced unit"] = True
    else:
        row["PO qty"] = row["PO unit"] = row["PO price"] = row["Received qty"] = "(no match)"
        if "LINE_NOT_ON_PO" in codes_present:
            cell_flags = {k: True for k in row}

    table_rows.append(row)
    style_rows.append(cell_flags)

if table_rows:
    df = pd.DataFrame(table_rows)

    def _highlight(_):
        styles = pd.DataFrame("", index=df.index, columns=df.columns)
        for i, flags in enumerate(style_rows):
            for col, flagged in flags.items():
                if flagged and col in styles.columns:
                    styles.loc[i, col] = "background-color: #5c1a1a"
        return styles

    # st.dataframe() silently fails to render a pandas Styler (an Arrow-
    # serialization issue, not a visible exception -- found live while
    # testing this page: the table area rendered completely empty, no
    # error in the Streamlit log). Rendering the Styler's own HTML
    # directly sidesteps that entirely.
    styled = (df.style.apply(_highlight, axis=None)
              .set_table_styles([{"selector": "th, td", "props": "padding: 6px 12px; text-align: left;"},
                                  {"selector": "table", "props": "width: 100%; border-collapse: collapse;"}])
              .hide(axis="index"))
    st.markdown(styled.to_html(), unsafe_allow_html=True)
else:
    st.caption("No line items extracted.")

st.divider()
st.subheader("Review decision")
reason = st.text_area("Reason (recorded in the audit trail)")
reviewer = st.session_state.get("reviewer_name", "reviewer")
bcol1, bcol2, bcol3 = st.columns(3)


def _decide(decision):
    if not reason.strip():
        st.error("Enter a reason before recording a decision.")
        return
    db.record_human_decision(conn, selected_id, decision, reviewer=reviewer, reason=reason)
    st.success("Recorded.")
    st.rerun()


if bcol1.button("✅ Approve", use_container_width=True):
    _decide("APPROVED")
if bcol2.button("❌ Reject", use_container_width=True):
    _decide("REJECTED")
if bcol3.button("⏸️ Hold", use_container_width=True):
    _decide("HELD")

st.divider()
st.subheader("Draft vendor query")
st.caption("Drafts an email requesting a corrected invoice or credit note, grounded in the "
           "findings above. You review and edit before sending it yourself -- nothing is sent "
           "automatically.")

draft_key = f"vendor_draft_{selected_id}"
if st.button("Draft email to vendor"):
    with conn.cursor() as cur:
        cur.execute("SELECT count FROM ap.api_usage WHERE day = CURRENT_DATE")
        row = cur.fetchone()
    used_today = row[0] if row else 0
    limit = daily_limit()
    if used_today >= limit:
        st.error(f"Today's shared API quota of {limit} has been reached. Try again tomorrow.")
    else:
        allowed, count = db.try_consume_daily_quota(conn, limit)
        if not allowed:
            st.error(f"Quota reached mid-request ({count}/{limit}). Try again tomorrow.")
        else:
            explanation = (investigate_entry["payload"].get("explanation") if investigate_entry
                            else "; ".join(present.format_detail_sentences(e["code"], e.get("detail"))
                                           for e in exceptions))
            draft = draft_vendor_query(extracted, [e["code"] for e in exceptions], explanation)
            st.session_state[draft_key] = draft
            db.log_audit(conn, selected_id, "agent:drafter", "draft_vendor_query",
                          payload={"subject": draft["subject"], "usage": draft["usage"]})

if draft_key in st.session_state:
    draft = st.session_state[draft_key]
    subject = st.text_input("Subject", value=draft["subject"])
    body = st.text_area("Body", value=draft["body"], height=200)
    if st.button("Mark as sent"):
        db.log_audit(conn, selected_id, f"user:{reviewer}", "vendor_query_sent",
                      payload={"subject": subject, "body": body})
        st.success("Marked as sent.")
        del st.session_state[draft_key]
        st.rerun()

st.divider()
with st.expander("How this was checked"):
    if investigate_entry:
        for call in investigate_entry["payload"].get("tool_calls", []):
            st.markdown(present.format_tool_call(call))
    else:
        st.caption("Not investigated -- this exception was raised directly by validation, "
                    "with no agent step needed to explain it.")

conn.close()
