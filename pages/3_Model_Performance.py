"""Everything engineering: extraction accuracy, exception precision/recall,
cost, and latency -- scored against the full labelled evaluation set. See
docs/eval_report.md (scripts/evaluate.py) for how every number here was
produced.
"""
import json
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from invoice_agent.utils import escape_dollars  # noqa: E402

st.set_page_config(page_title="Model Performance", page_icon="🧪", layout="wide")
st.title("Model Performance")

SUMMARY_PATH = ROOT / "docs" / "eval_summary.json"
if not SUMMARY_PATH.exists():
    st.info("No evaluation summary found yet -- run `python scripts/evaluate.py`.")
    st.stop()

summary = json.loads(SUMMARY_PATH.read_text())

st.caption(f"Scored against {summary['n_invoices']} hand-labelled invoices.")

acc = summary["accuracy_overall"]
by_layout = summary["accuracy_by_layout"]
hardest = min(by_layout.items(), key=lambda kv: kv[1]["accuracy"]) if by_layout else None

col1, col2, col3, col4 = st.columns(4)
col1.metric("False approvals", f"{summary['false_approvals']} / {summary['n_actual_exception']}",
            help="The headline requirement: must be zero.")
col2.metric("False rejections", f"{summary['false_rejections']} / {summary['n_actual_clean']}",
            help="Clean invoice flagged as an exception -- costs review time, not money.")
col3.metric("Field accuracy, overall", f"{acc['accuracy']:.1%}")
col4.metric(f"Hardest layout ({hardest[0]})" if hardest else "Hardest layout",
            f"{hardest[1]['accuracy']:.1%}" if hardest else "n/a")

st.divider()
st.subheader("Extraction accuracy by document layout")
st.dataframe(
    [{"Layout": layout, "Correct / Total": f"{v['correct']} / {v['total']}", "Accuracy": f"{v['accuracy']:.1%}"}
     for layout, v in by_layout.items()],
    use_container_width=True, hide_index=True,
)
chart_path = ROOT / "docs" / "eval_charts" / "accuracy_by_layout.png"
if chart_path.exists():
    st.image(str(chart_path))

st.divider()
st.subheader("Per-issue precision / recall")
pr = summary["precision_recall_by_code"]
if pr:
    st.dataframe(
        [{"Code": code, "TP": v["tp"], "FP": v["fp"], "FN": v["fn"],
          "Precision": f"{v['precision']:.0%}" if v["precision"] is not None else "n/a",
          "Recall": f"{v['recall']:.0%}" if v["recall"] is not None else "n/a"}
         for code, v in pr.items()],
        use_container_width=True, hide_index=True,
    )
f1_chart = ROOT / "docs" / "eval_charts" / "precision_recall_f1.png"
if f1_chart.exists():
    st.image(str(f1_chart))

st.divider()
st.subheader("Prompt-injection resistance")
inj = summary["prompt_injection_results"]
if inj:
    passed = sum(1 for r in inj if r["pass"])
    st.metric("Resisted embedded auto-approve instructions", f"{passed} / {len(inj)}")
else:
    st.caption("No prompt-injection test cases in this run.")

st.divider()
st.subheader("Explanation groundedness")
g = summary["groundedness"]
if g["investigated"]:
    st.metric("Grounded explanations", f"{g['grounded']} / {g['investigated']}",
              help="Every number in the explanation traces back to a tool call or the invoice "
                   "itself, per a heuristic check -- see docs/decisions_log.md for what it can't catch.")
else:
    st.caption("No investigated exceptions in this run.")

st.divider()
st.subheader("Cost")
cost = summary["cost"]
ccol1, ccol2, ccol3 = st.columns(3)
ccol1.metric("Extraction", f"SGD {float(cost['extraction']):,.2f}")
ccol2.metric("Investigation", f"SGD {float(cost['investigation']):,.2f}")
ccol3.metric("Per invoice", f"SGD {float(cost['per_invoice']):,.4f}")
st.bar_chart({"Cost (SGD)": {"Extraction": float(cost["extraction"]),
                              "Investigation": float(cost["investigation"])}})
st.caption(escape_dollars(
    "Extraction runs on claude-haiku-4-5 for typed documents, claude-sonnet-5 for scans -- "
    "investigation (only on exceptions) always runs on claude-sonnet-5."))

st.divider()
st.subheader("Latency")
timing = summary["timing"]
tcol1, tcol2 = st.columns(2)
for col, key, label in [(tcol1, "no_investigation", "Without investigation"),
                         (tcol2, "with_investigation", "With investigation")]:
    t = timing.get(key)
    if t:
        col.metric(label, f"{t['mean']:.2f}s mean", help=f"{t['n']} invoices, {t['median']:.2f}s median")
    else:
        col.metric(label, "no timing data", help="Rows reused via --resume carry no elapsed time.")
