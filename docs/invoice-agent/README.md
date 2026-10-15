# AI Invoice Exception Agent

An accounts-payable assistant that reads vendor invoices, runs a
**three-way match** against SAP-style purchasing data (PO ↔ goods receipt ↔ invoice),
validates **Singapore GST**, routes exceptions to a human reviewer with a
plain-English explanation, and records every step in an **append-only audit trail**.

It mirrors the vendor invoice management workflow finance teams run on ERP
systems. Extraction (reading unstructured invoices) and exception explanation
use an LLM; matching, tax checks, and the approve/reject decision are plain
deterministic code — an LLM never does arithmetic or approves anything.
One bounded, read-only tool-calling agent investigates *why* an exception
happened (see `docs/design_decisions.md`); everything else is a fixed pipeline,
not a multi-agent framework.

**Status: work in progress, being rebuilt section by section — see the
roadmap below.** This README will be rewritten fully once the pipeline is real.

## How it works

```
invoice PDF ──► Extract ──► Match (PO / GR) ──► Tax check ──► Decide ──► Auto-approve
               (Claude,      (SQL against       (GST rate,     │
                vision for    mock SAP tables)   reg. no.,      └──► Exception queue
                scans)                           arithmetic)          (human review)
                       every step ──► ap.audit_log
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

Full rule definitions and SAP/IRAS sourcing: `docs/data_sources.md`.

## Data

All data is synthetic and the companies are fictional.

- `data/sap/` holds mock SAP tables using real S/4HANA names: `LFA1` (vendors,
  with address/payment terms/blocked flag), `EKKO`/`EKPO` (purchase orders, with
  price unit, GR tolerance, GR-based invoice verification flag, tax code, service
  lines), `MSEG` (goods movements, incl. returns), `EKBE` (PO history — cumulative
  GR/invoice quantities), `RBKP`/`RSEG` (historical posted invoices, for
  duplicate detection). See `docs/data_sources.md` for what each field means and
  where that definition comes from, including the places this is a deliberate
  simplification of real SAP behaviour.
- `data/invoices/` holds 216 invoices: a "realistic" set (150, ~88% clean) and a
  hand-authored "edge case" set (~66, at least 3 examples of each failure mode —
  combined exceptions, tolerance boundaries, price-unit confusion, UoM mismatch,
  sequential partial invoices, service lines with no GR, unknown/blocked vendors,
  near-duplicates, multi-PO invoices, credit notes, unmatched freight lines,
  foreign currency, GST rounding variance, messy scans, and prompt-injection
  probes). 5 layouts (one deliberately keyless — see `docs/design_decisions.md`
  for how line matching handles it) plus a scanned/OCR variant, and a few
  simplified e-invoice (Peppol/InvoiceNow-style) JSON samples.
- `data/invoices/ground_truth.json` has the expected fields, decision and exception
  codes for every invoice, used to score the pipeline. Written atomically after
  every invoice during generation, so a crash partway through can't corrupt or
  lose it.

## Quick start

```bash
cp .env.example .env               # add your ANTHROPIC_API_KEY
docker compose up -d               # Postgres
pip install -r requirements.txt

python scripts/generate_mock_sap.py
python scripts/generate_invoices.py
python scripts/load_db.py
```

## Roadmap

- [x] Mock SAP purchasing data + Postgres schema
- [x] Labelled invoice dataset (16 scenarios, 4 layouts) — being expanded, see below
- [ ] Deterministic validation engine (plain Python/SQL) + pytest
- [ ] Extraction (Claude, structured output, vision for scans)
- [ ] Exception investigation agent (bounded, read-only tools)
- [ ] Audit log + human approve/reject
- [ ] Evaluation script scored against ground truth
- [ ] Streamlit review queue with approve/reject + audit trail
- [ ] Deploy + demo video
