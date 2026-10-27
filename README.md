# Invoice Exception Agent

An AI-assisted invoice review system that helps finance teams identify billing errors before payment. It combines Claude for reading invoices and investigating issues with rule-based checks for purchase order matching and Singapore GST compliance.

**Live demo:** https://invoice-exception-agent.streamlit.app/

![Invoice Review](docs/screenshots/invoice_review.png)

### Results

Evaluated on 217 real invoices across 7 document layouts:

- **0 false approvals across 61 exceptions.** No invoice with a known issue was incorrectly approved.
- **95.6% field-level extraction accuracy** across the evaluation set.
- **$0.023 average API cost per invoice**, including extraction and exception investigation.

## How it works

When a company receives an invoice, the finance team needs to check that the amount billed matches what was ordered and delivered. This is known as *three-way matching*, and it's one of the main checks performed before paying a vendor.

I built this project to explore how AI could help automate that process without relying on an LLM to make financial approval decisions.

The system works in four stages:

1. **Extract invoice details.** Claude reads the invoice PDF and extracts structured information such as the vendor, invoice number, line items, amounts, and taxes.
2. **Validate against purchasing records.** Python checks the extracted information against purchase orders and goods receipts, using SAP-style purchasing data. It also checks Singapore GST calculations at 9%.
3. **Decide whether the invoice can be approved.** Invoices that pass all required checks can be automatically approved. Missing information, mismatches, or failed checks are flagged for review. Approval decisions are made entirely through deterministic rules, not by Claude.
4. **Investigate exceptions.** A separate Claude-powered agent uses read-only tools to look into flagged issues and explain what went wrong. A human reviewer can then approve, reject, hold, or request a corrected invoice from the vendor.

The investigation agent has a strict tool-call limit and cannot approve or reject invoices itself.


The main design principle is to keep AI responsible for tasks where it is useful, such as reading unstructured documents and explaining exceptions, while leaving financial checks and approval logic to predictable, testable code.

## Run it locally

Clone the repository and set up your environment:

```bash
cp .env.example .env              # add your ANTHROPIC_API_KEY, set a local DATABASE_URL
docker compose up -d              # Postgres
pip install -r requirements.txt
```

Generate sample purchasing records and invoices, then load them into the database:

```bash
python scripts/generate_mock_sap.py
python scripts/generate_invoices.py
python scripts/load_db.py
```

Run the tests and launch the Streamlit app:

```bash
pytest                             # 102 tests
streamlit run app.py               # http://localhost:8501
```

## Evaluation

I tested the system on 217 real invoices covering 7 different document layouts, with a particular focus on avoiding incorrect approvals.

The evaluation also covers extraction accuracy by layout, exception detection precision and recall, prompt-injection resistance, API cost, and processing latency.

The full results are available on the **Model Performance** page of the [live demo](https://invoice-exception-agent.streamlit.app/).

More detailed documentation covering the architecture, SAP and GST field mappings, and design decisions is maintained locally within the project.

## Limitations

This is a personal project and proof of concept, not a production accounts-payable system.

- **No live ERP integration.** Purchasing records are synthetic and modelled after SAP tables. The system does not connect to a real company's SAP environment.
- **Singapore GST only.** Tax validation currently assumes a flat 9% GST rate.
- **Human oversight for exceptions.** The investigation agent can explain problems and suggest resolutions, but cannot approve or reject invoices.
- **Limited evaluation scope.** Results come from 217 invoices, so they should not be treated as representative of all invoice formats or real-world conditions. The Model Performance page includes a breakdown of where the system performs less reliably.
- **Non-production hosting.** The hosted database uses Databricks Lakebase Free Edition, which is intended for personal and non-commercial use. The current deployment is for demonstration purposes.
