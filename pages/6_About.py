"""About: one architecture diagram, what's AI vs. deterministic, and this
project's known limitations. Everything else lives in docs/ for anyone
who wants more detail -- this page is the three-sentence version.
"""
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

st.set_page_config(page_title="About", page_icon="ℹ️", layout="wide")
st.title("About")

st.markdown(
    "An accounts-payable exception review system: Claude reads unstructured invoices into "
    "structured fields and explains *why* an exception happened; every pass/fail decision "
    "on money is plain deterministic Python checking a three-way match (invoice vs. purchase "
    "order vs. goods receipt) and Singapore GST rules. The system never approves anything on "
    "its own judgment -- anything that doesn't pass every check goes to a human."
)

SVG = """
<svg viewBox="0 0 980 230" xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:980px;height:auto;font-family:sans-serif">
  <defs>
    <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">
      <path d="M0,0 L8,3 L0,6 Z" fill="#888"/>
    </marker>
  </defs>
  <style>
    .box { stroke-width: 1.5; rx: 8; }
    .ai { fill: #1e3a5f; stroke: #5b9bd5; }
    .det { fill: #1f3d2b; stroke: #6abf69; }
    .human { fill: #4a3012; stroke: #d9a441; }
    .label { fill: #e8e8e8; font-size: 14px; text-anchor: middle; }
    .sub { fill: #aaa; font-size: 11px; text-anchor: middle; }
    .edge { stroke: #888; stroke-width: 1.5; fill: none; marker-end: url(#arrow); }
  </style>

  <rect x="10" y="80" width="140" height="60" class="box ai"/>
  <text x="80" y="105" class="label">Extract</text>
  <text x="80" y="122" class="sub">Claude</text>

  <rect x="190" y="80" width="140" height="60" class="box det"/>
  <text x="260" y="105" class="label">Match + GST</text>
  <text x="260" y="122" class="sub">deterministic</text>

  <rect x="370" y="80" width="140" height="60" class="box det"/>
  <text x="440" y="105" class="label">Decide</text>
  <text x="440" y="122" class="sub">deterministic</text>

  <rect x="550" y="10" width="160" height="60" class="box det"/>
  <text x="630" y="35" class="label">Auto-approve</text>
  <text x="630" y="52" class="sub">no human touch</text>

  <rect x="550" y="150" width="160" height="60" class="box ai"/>
  <text x="630" y="175" class="label">Investigate</text>
  <text x="630" y="192" class="sub">Claude, read-only</text>

  <rect x="760" y="150" width="200" height="60" class="box human"/>
  <text x="860" y="175" class="label">Human review</text>
  <text x="860" y="192" class="sub">approve / reject / hold</text>

  <path class="edge" d="M150,110 L190,110"/>
  <path class="edge" d="M330,110 L370,110"/>
  <path class="edge" d="M440,80 L440,55 L550,40"/>
  <path class="edge" d="M440,140 L440,170 L550,180"/>
  <path class="edge" d="M710,180 L760,180"/>
</svg>
"""
st.markdown(SVG, unsafe_allow_html=True)

st.divider()
st.subheader("Limitations")
st.markdown("""
- No real SAP/ERP connection -- the purchasing data is synthetic, shaped like real SAP tables, not a live integration.
- Singapore GST only, at a flat 9% rate -- no multi-currency tax handling beyond that.
- The investigation agent proposes a resolution; it never has approve/reject authority. That's deliberate, not a missing feature.
- Scored against a real but bounded evaluation set -- see the Model Performance page for exactly where accuracy is weakest, rather than a claim of uniform accuracy.
- The hosted database (Databricks Lakebase, Free Edition) is scoped to personal/non-commercial use -- not a production deployment pattern as-is.
""")
