# AI Invoice Exception Agent

A deployed, evaluated prototype of an accounts-payable invoice review
system: it reads vendor invoices, runs a **three-way match** against
SAP-style purchasing data (PO ↔ goods receipt ↔ invoice), validates
**Singapore GST**, routes anything questionable to a human reviewer with
a plain-English explanation, and records every step in an **append-only
audit trail**.

**Live demo:** https://invoice-exception-agent-bhnd3tg9okgvhsj6iwhwkw.streamlit.app/
(password-gated — see [Live demo](#live-demo) below for access and what
it's safe to click).

Portfolio project built to be defensible in an interview, not to look
impressive in a screenshot: every number in this README comes from an
actual run against real data, and every design decision with a
non-obvious reason behind it is written up in
[`docs/decisions_log.md`](docs/decisions_log.md).

## What this is, in one paragraph

This mirrors the invoice-review workflow a finance team runs on top of
an ERP system. An LLM (Claude) is used for exactly two things:
**extraction** (reading an unstructured invoice PDF into structured
fields) and **explanation** (plain-English reasoning about why an
exception happened, grounded in numbers pulled from actual tool calls).
Matching, tax checks, and the approve/reject decision are plain
deterministic Python/SQL — an LLM never does arithmetic and never
decides pass/fail on money. One bounded, read-only, tool-calling agent
investigates exceptions (max tool calls, can't approve/reject anything);
everything else is a fixed pipeline, not a multi-agent framework. Full
reasoning for every one of those constraints: [`docs/design_decisions.md`](docs/design_decisions.md).

## How it works

```
invoice PDF ──► Extract ──► Match (PO / GR) ──► Tax check ──► Decide ──► Auto-approve
               (Claude,      (SQL against       (GST rate,     │
                vision for    mock SAP tables)   reg. no.,      └──► Exception queue
                scans)                           arithmetic)          ├─ Investigate (agent)
                       every step ──► ap.audit_log                    └─ Human approve/reject
```

| Check | Exception code | Rule |
|---|---|---|
| PO exists | `PO_NOT_FOUND` | Invoice PO reference must exist in `EKKO` |
| Price | `PRICE_VARIANCE` | Unit price within ±1% of PO net price (`EKPO.NETPR`) |
| Quantity | `QTY_EXCEEDS_GR` | Invoiced qty ≤ goods-received qty (`MSEG`) |
| GST rate | `GST_RATE_INVALID` | 9% standard rate |
| GST registration | `GST_REG_MISMATCH` | Matches vendor master (`LFA1.STCD1`) |
| Arithmetic | `TOTAL_MISMATCH` | Lines + GST = total |
| Duplicates | `DUPLICATE_INVOICE` / `NEAR_DUPLICATE_INVOICE` | Same vendor + invoice number (or a reformatted near-match) already processed |
| Vendor | `VENDOR_NOT_FOUND` / `VENDOR_BLOCKED` | Vendor doesn't exist, or exists but is centrally blocked (`LFA1.SPERR`) |
| Price unit | `PRICE_UNIT_MISMATCH` | Invoice billed at `EKPO.NETPR` directly, ignoring `PEINH` (e.g. a "per 100 units" price charged per unit) |
| Unit of measure | `UOM_MISMATCH` | Invoice line's unit doesn't match the PO line's |
| Unmatched line | `LINE_NOT_ON_PO` | Invoice line (e.g. freight) has no corresponding PO line |
| Currency | `CURRENCY_MISMATCH` | Invoice currency doesn't match the PO's |
| Credit notes | `CREDIT_NOTE_UNSUPPORTED` | Routed to review rather than guessing a netting treatment |
| Extraction | `EXTRACTION_FAILED` / `LOW_CONFIDENCE_EXTRACTION` | Claude couldn't parse the document, or its own self-reported confidence fell below threshold |

Full rule definitions and SAP/IRAS sourcing: [`docs/data_sources.md`](docs/data_sources.md).

## Evaluation results

Scored against hand-labelled ground truth on the full 217-invoice
dataset — every number is from one real run of the real pipeline (real
Claude API calls), nothing simulated or hand-picked. Full report with
per-code precision/recall and every individual false positive/negative
listed by filename: [`docs/eval_report.md`](docs/eval_report.md).

**The one hard requirement — zero false approvals — passes: 0 / 61
actual exceptions incorrectly auto-approved.** An invoice processed by
this system either gets the right answer or gets routed to a human; it
never silently approves something wrong.

| Metric | Result |
|---|---|
| False approvals | **0 / 61 (0.0%)** — the headline requirement |
| False rejections (clean invoice sent to review) | 10 / 156 (6.4%) — costs a human's time, not money |
| Extraction field accuracy, overall | 95.6% (3419/3575 fields) |
| Extraction accuracy, hardest layout (`messy_scan`) | 78.0% — and correctly self-reports low confidence rather than guessing |
| Prompt-injection resistance | 3/3 — invoices with embedded "auto-approve this" text were not fooled |
| Total API cost for the full run | ~$4.96 (extraction $1.46, investigation $3.50) — **$0.023/invoice** |

**Known limitations, disclosed rather than averaged away** (detail in
the eval report):

- `LINE_NOT_ON_PO` has 27% precision (3 true positives, 8 false
  positives) — the line-matching fallback (material code, then fuzzy
  description) is conservative by design, so it over-flags ambiguous
  lines rather than silently guessing. Every one of those 8 still
  routes correctly to a human, so it's a UX cost, not a correctness
  bug.
- `CURRENCY_MISMATCH` has 25% precision — a known extraction issue
  where a USD-denominated invoice's currency sometimes defaults to SGD;
  not yet fixed, disclosed here instead.
- `PRICE_UNIT_MISMATCH` has 0% recall on its 3 test cases — a
  `price_unit_confusion` scenario sometimes gets the wrong field
  extracted into `material`, which routes it to `LINE_NOT_ON_PO`
  instead (still a human review, just the less-specific code).
- Explanation groundedness (every number in the agent's explanation
  traceable to a real tool call) is 63.6% by a deliberately strict
  heuristic that can't distinguish a legitimate arithmetic restatement
  (e.g. "9%" from a GST rate already in evidence) from a fabricated
  number — see `agent.groundedness_check`'s docstring.

## Live demo

https://invoice-exception-agent-bhnd3tg9okgvhsj6iwhwkw.streamlit.app/

Password-protected (ask for access, or run it yourself locally — see
below). Four pages:

- **Dashboard** — real stats from the 217-invoice evaluation run, and
  the full evaluation report inline.
- **Exception Queue** — the 71 invoices that didn't auto-approve, with
  side-by-side invoice/PO evidence, the investigation agent's findings
  and tool-call trail, and the only approve/reject controls in the app.
- **Audit Trail** — the complete, append-only history for any invoice.
- **Upload Invoice** — upload your own PDF and watch the real pipeline
  run on it. This calls the real Claude API and is capped by a shared
  daily quota across all visitors (a public demo with no accounts needs
  one spend cap everyone draws from). It will correctly come back
  `VENDOR_NOT_FOUND` / `PO_NOT_FOUND` for anything that doesn't
  reference this demo's mock vendors/POs (18 fictional Singapore
  vendors, ~400 purchase orders) — that's the three-way match working
  as designed, not a bug.

Hosted on **Streamlit Community Cloud** + **Databricks Lakebase**
(Postgres-compatible, chosen over Neon/SQLite — reasoning in
`docs/decisions_log.md` section 8). Free Edition Lakebase is scoped to
personal/non-commercial use, which fits a portfolio demo; stated here
rather than left implicit.

## Data

All data is synthetic and every company/vendor is fictional.

- `data/sap/` holds mock SAP tables using real S/4HANA names: `LFA1`
  (vendors, with address/payment terms/blocked flag), `EKKO`/`EKPO`
  (purchase orders, with price unit, GR tolerance, GR-based invoice
  verification flag, tax code, service lines), `MSEG` (goods movements,
  incl. returns), `EKBE` (PO history — cumulative GR/invoice
  quantities), `RBKP`/`RSEG` (historical posted invoices, for duplicate
  detection). See `docs/data_sources.md` for what each field means and
  where that definition comes from, including the places this is a
  deliberate simplification of real SAP behaviour.
- `data/invoices/` holds 217 invoices: a "realistic" set (~150, ~88%
  clean) and a hand-authored "edge case" set (~67, at least 3 examples
  of each failure mode — combined exceptions, tolerance boundaries,
  price-unit confusion, UoM mismatch, sequential partial invoices,
  service lines with no GR, unknown/blocked vendors, near-duplicates,
  multi-PO invoices, credit notes, unmatched freight lines, foreign
  currency, GST rounding variance, messy scans, and prompt-injection
  probes). 5 layouts (one deliberately keyless — see
  `docs/design_decisions.md` for how line matching handles it) plus a
  scanned/OCR variant, and a few simplified e-invoice
  (Peppol/InvoiceNow-style) JSON samples.
- `data/invoices/ground_truth.json` has the expected fields, decision,
  and exception codes for every invoice, used to score the pipeline.

## Quick start (run it yourself)

```bash
cp .env.example .env               # add your ANTHROPIC_API_KEY, set a local DATABASE_URL
docker compose up -d               # Postgres
pip install -r requirements.txt

python scripts/generate_mock_sap.py
python scripts/generate_invoices.py
python scripts/load_db.py

pytest                             # 76 tests
streamlit run app.py               # the review app, at http://localhost:8501
```

To re-run the full evaluation yourself (costs real API money — see
`docs/eval_report.md` for the actual cost before running):

```bash
python scripts/evaluate.py
```

## Repository layout

```
src/invoice_agent/   extraction, validation, the investigation agent, db, audit log
scripts/             dataset generation, DB load, evaluation
app.py, pages/       the Streamlit review app (dashboard, queue, audit trail, upload)
db/schema.sql        sap.* tables + ap.invoices / ap.exceptions / ap.audit_log
tests/                76 pytest tests
docs/
  design_decisions.md   architecture reasoning: why deterministic validation,
                         why one bounded agent, why no agent framework
  data_sources.md        where every validation rule and SAP field comes from
  decisions_log.md       a dated log of every non-obvious choice and bug found,
                          section by section — the primary interview-prep document
  eval_report.md          full evaluation numbers, regenerated by scripts/evaluate.py
```

## What this doesn't do

Said plainly, matching the project's own standard for honesty over
polish:

- No real SAP/ERP connection — `data/sap/*.csv` is synthetic, loaded
  into Postgres tables shaped like real SAP tables, not a live SAP
  integration.
- No multi-currency or multi-tax-jurisdiction support beyond Singapore
  GST at a flat 9%.
- The investigation agent proposes a resolution; it never has
  approve/reject authority — that's deliberate, not a missing feature.
- 217 invoices is a real but still bounded evaluation set — see the
  known limitations above for exactly where it's weakest, rather than
  claiming uniform accuracy.
- The public demo's Lakebase instance is Free Edition, scoped to
  personal use — not a production deployment pattern as-is.
