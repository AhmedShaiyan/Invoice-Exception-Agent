"""Manager view: straight-through rate, where exceptions come from, which
vendors cause the most of them, and how much value is currently on hold.
Our own data only -- no external benchmarks.
"""
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from invoice_agent import codes as C  # noqa: E402
from invoice_agent import db  # noqa: E402

st.set_page_config(page_title="Insights", page_icon="📊", layout="wide")
st.title("Insights")

conn = db.connect()
stats = db.dashboard_stats(conn)
by_code = db.exceptions_by_code(conn)
vendors = db.vendor_scorecard(conn)
by_day = db.value_on_hold_by_day(conn)
conn.close()

total = stats["total"]
auto_approved = stats["by_decision"].get("AUTO_APPROVE", 0)
straight_through_rate = (auto_approved / total * 100) if total else 0.0

col1, col2 = st.columns(2)
col1.metric("Straight-through rate", f"{straight_through_rate:.1f}%",
            help=f"{auto_approved} of {total} invoices passed every check with no human involved.")
col2.metric("Total invoices on file", total)

st.divider()
st.subheader("Exceptions by issue type")
if by_code:
    chart_data = {C.PLAIN_LABELS.get(r["code"], r["code"]): r["n"] for r in by_code}
    st.bar_chart(chart_data)
else:
    st.caption("No exceptions on file.")

st.divider()
st.subheader("Top vendors by exception volume")
if vendors:
    st.dataframe(
        [{"Vendor": v["vendor_name"] or "(unknown)", "Exceptions": v["exception_count"],
          "Value SGD": f"{v['exception_value']:,.2f}"} for v in vendors],
        use_container_width=True, hide_index=True,
    )
else:
    st.caption("No exceptions on file.")

st.divider()
st.subheader("Value on hold over time")
if len(by_day) > 1:
    st.bar_chart({"Value on hold (SGD)": {r["day"].isoformat(): float(r["value"]) for r in by_day}})
elif by_day:
    st.caption(f"All current exceptions were processed on {by_day[0]['day'].isoformat()} -- "
               f"SGD {float(by_day[0]['value']):,.2f} on hold. A multi-day trend will show here "
               f"once invoices are processed across more than one day.")
else:
    st.caption("No open exceptions.")
