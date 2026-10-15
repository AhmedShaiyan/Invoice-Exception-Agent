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
| Duplicates | `DUPLICATE_INVOICE` | Same vendor + invoice number already processed |

## Data

All data is synthetic and the companies are fictional.

- `data/sap/` holds mock SAP tables using real S/4HANA names: `LFA1` (vendors),
  `EKKO`/`EKPO` (purchase orders), `MSEG` (goods receipts), with some partial deliveries.
- `data/invoices/` holds 16 invoice PDFs in three layouts plus one scanned image-only PDF,
  each tied to a scenario.
- `data/invoices/ground_truth.json` has the expected fields, decision and exception codes
  for every invoice, used to score the agent.

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
