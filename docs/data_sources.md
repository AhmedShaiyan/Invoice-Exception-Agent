# Data sources

Every SAP field and every GST rule this project models is listed here with
what it means, why it's included, and where the definition comes from.
Confidence is marked per claim:

- **Primary** — an official SAP Help Portal page, an SAP Note, or an
  iras.gov.sg / docs.peppol.eu page, confirmed by direct fetch or by two
  independent search passes returning matching, specific detail (exact
  dollar figures, exact field names) that would be unlikely to coincide if
  fabricated.
- **Secondary** — SAP Community (sometimes SAP-employee-authored),
  DDIC-mirror sites (sapdatasheet.org, se80.co.uk, leanx.eu — these
  reproduce SAP's own data-dictionary short text, not opinion, but aren't
  official), or similarly consistent non-primary sources.
- **Not found** — I looked and could not locate an authoritative source.
  Where this happens the project uses the simpler, undocumented version
  rather than a guessed rule, and says so at the point of use.

Research pass done 2026-10-15 (web search + fetch, cross-checked where the
fetch tool couldn't render a page directly — see notes per item).

## A. SAP MM / S/4HANA

### A1. EKPO fields added this round

| Field | Meaning | Confidence | Source |
|---|---|---|---|
| `PEINH` (price unit) | Denominator for `NETPR`. A price of 250 with `PEINH=100` means 2.50 per unit of measure — lets SAP store fractional-cent precision (e.g. price per 100 units). This project uses it for the `MAT-8001` GPU-compute line (price per node-hour, no fractional issue) and a new catalogue item priced per 100 units, to create a realistic "price-unit confusion" exception where an invoice bills the `NETPR` figure as if it were per-unit. | Secondary | [sapdatasheet.org — EKPO-PEINH](https://www.sapdatasheet.org/abap/tabl/ekpo-peinh.html) |
| `UEBTO` / `UNTTO` (over-/under-delivery tolerance %) | Percentage tolerance on **quantity** at goods receipt — NOT a price tolerance. I originally assumed these covered invoice price variance; the research corrected that. They're used here only to decide, per PO line, how much over/under receipt is "normal" before the line should be flagged — i.e. they bound what counts as a legitimate partial/over receipt in `MSEG`/`EKBE`, not the invoice price check. | Secondary | [se80.co.uk — EKPO-UEBTO](https://www.se80.co.uk/sap-table-fields/?tabname=ekpo&fieldname=uebto), [EKPO-UNTTO](https://www.se80.co.uk/sap-table-fields/?tabname=ekpo&fieldname=untto) |
| `WEBRE` (GR-based invoice verification flag) | When set, an invoice for that PO line must reference a specific goods-receipt document rather than drawing generically against the PO. Modeled here as a boolean per line; service lines (no GR expected) always have it unset, matching real SAP config. | Secondary/semi-official | [SAP wiki — "What does GR-BASED IV indicator mean"](https://wiki.scn.sap.com/wiki/display/ERPSCM/What+does+GR-BASED+IV+indicator+mean), [SAP Community](https://community.sap.com/t5/enterprise-resource-planning-blog-posts-by-sap/gr-based-iv-why-is-that-so-important-on-procurement-invoices/ba-p/13561350) |
| `MWSKZ` (tax code) | Tax code set on the PO line, re-proposed (and in principle overridable) at invoice entry. Used here as the expected GST treatment code against which the invoice's claimed rate is checked. | Primary URL / secondary body | [help.sap.com — Taxes in Purchasing](https://help.sap.com/docs/SUPPORT_CONTENT/spmm/3362168111.html) (page confirmed via search indexing; JS-rendered, body not independently fetched) |

**Price tolerance correction**: real SAP does not use an EKPO field for
invoice price variance at all — it uses company-code-level **tolerance
keys** (transaction `OMR6`), notably key `PP`, with separate
percentage/absolute limits, and an invoice outside tolerance still posts
but with a payment block rather than outright rejection. This project keeps
the simpler fixed **±1% hardcoded tolerance, documented explicitly as a
simplification** of the real `OMR6`/`PP` mechanism, rather than
implementing configurable tolerance keys — out of scope for a demo, and the
simplification is now written down instead of silently assumed.
Source (secondary, well-corroborated): [SAP Community — Price Variance (PP) – MIRO](https://community.sap.com/t5/enterprise-resource-planning-q-a/price-variance-pp-miro/qaq-p/6468386).

### A2. MSEG / MATDOC

In S/4HANA, goods-movement data lives in a single table **MATDOC**; `MKPF`
and `MSEG` are no longer populated directly and are exposed as
compatibility CDS views over `MATDOC` for old code that still reads them.
This project keeps the `MSEG` name (readers coming from an SAP background
will recognize it immediately) but documents the real S/4HANA structure
here rather than implying `MSEG` is still a base table.

**Confidence: Primary.** SAP Note 2206980 ("Material Inventory Management:
change of data model in S/4HANA") — the official note is login-gated at
`launchpad.support.sap.com`; verified via a public mirror:
[kodyaz.com PDF of SAP Note 2206980](https://www.kodyaz.com/downloads/SAP-Note-2206980.pdf),
corroborated by an SAP-employee-authored community post:
[SAP Community — S/4HANA Inventory Management Tables (NSDM)](https://community.sap.com/t5/enterprise-resource-planning-blog-posts-by-sap/sap-s-4hana-inventory-management-tables-new-simplified-data-model-nsdm/ba-p/13497469).

### A3. Movement types

`101` = goods receipt for a purchase order. `122` = return delivery to
vendor (reversal of a prior `101`, posted via MIGO). This round adds `122`
so the dataset can include a returned-goods scenario. **Confidence:
Primary** (official help.sap.com pages identified by URL; body text not
independently re-fetched due to JS rendering, but corroborated by several
independent secondary sources agreeing on identical semantics):
[Movement Type Concept](https://help.sap.com/docs/SAP_ERP/96bf9ad642cf4b26a29595e3d573fb8c/1663bd534f22b44ce10000000a174cb4.html),
[Entering a GR for a PO](https://help.sap.com/docs/SAP_ERP/96bf9ad642cf4b26a29595e3d573fb8c/a663bd534f22b44ce10000000a174cb4.html),
[Return Delivery](https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE/91b21005dded4984bcccf4a69ae1300c/e263bd534f22b44ce10000000a174cb4.html).

### A4. EKBE — PO history (why the quantity check now uses this, not raw MSEG)

Real SAP tracks **cumulative** goods-received and invoice-received quantity
per PO line in table `EKBE` ("History per Purchasing Document"), one row
per posting event, classified by `VGABE` (1 = goods receipt, 2 = invoice
receipt, among others) and `BEWTP`. The pre-aggregated totals
(`EKBES.WEMNG` = cumulative received, `REMNG` = cumulative invoiced) are
what SAP actually compares against at invoice entry — not a single GR
document. This project's old qty check compared one invoice to one line's
total `MSEG` receipt, which cannot correctly handle **sequential partial
invoices against the same line** (invoice 1 for qty 5, invoice 2 for qty 5,
against a line received at qty 8 — each invoice alone looks fine, together
they overshoot). The new `ekbe` table stores one row per GR or invoice
event (`vgabe` 1 or 2) per PO line, and the validation engine sums it
per line to get the cumulative figures, mirroring real SAP.

**Confidence: Secondary only** — I could not find a help.sap.com page
describing the cumulative-aggregation behavior explicitly (the user-facing
surface is the "Purchase Order History" tab in ME23N, whose help page I
could not fetch the body of either). The field semantics are consistent
across independent technical references:
[EKBE field list — beyondse16.com](https://beyondse16.com/2020/04/16/complete-field-list-of-sap-table-ekbe-history-per-purchasing-document/),
[EKBES — leanx.eu](http://leanx.eu/en/sap/table/ekbes.html).

### A5. RBKP / RSEG — invoice documents, and the real duplicate-check field

`RBKP` is the header table for an incoming invoice posted via MIRO
(Logistics Invoice Verification); `RSEG` is its line items. Key header
fields: `BELNR` (invoice doc number), `LIFNR` (vendor), **`XBLNR`**
(vendor's own/reference invoice number printed on the paper invoice).
**`XBLNR` is the field SAP's standard duplicate-invoice check
(`OMRDC` config) actually compares** — company code + vendor + currency +
`XBLNR` if a reference was entered, else company code + vendor + currency +
date + gross amount as a fallback. This project's duplicate check mirrors
that: same vendor + same `XBLNR`-equivalent (the invoice number as printed)
is the primary signal, with a near-duplicate heuristic (see
`docs/design_decisions.md`) layered on top for reformatted numbers.

**Confidence: Secondary**, consistent across multiple independent SAP
Community threads, matching documented `OMRDC` behavior, no help.sap.com
body text located:
[SAP Community — Check Invoice Duplication](https://community.sap.com/t5/enterprise-resource-planning-q-a/check-invoice-duplication/qaq-p/8000762),
[technosap.com — How Duplicate Invoice Check Works](https://www.technosap.com/sap-fi-co-financial-accounting-controlling/how-duplicate-invoice-check-works-sap/).

## B. Singapore GST (IRAS)

### B6. Standard rate: 9%, effective 1 Jan 2024

**Confidence: Primary.** GST rose 7%→8% (1 Jan 2023) then 8%→9%
(1 Jan 2024). [IRAS — Overview of GST Rate Change](https://www.iras.gov.sg/taxes/goods-services-tax-(gst)/gst-rate-change/gst-rate-change-for-business/overview-of-gst-rate-change).

### B7. Required tax invoice fields

**Confidence: Primary**, confirmed by two independent web searches against
`iras.gov.sg` returning identical, specific field lists.

A **regular tax invoice** (total payable > S$1,000) must show: the words
"Tax Invoice"; supplier's name and address; supplier's GST registration
number; invoice date; an invoice identifying number; customer's name;
customer's address; description of goods/services; GST rate; total amount
payable excluding GST; total GST amount; total amount payable including
GST.

A **simplified tax invoice** is permitted when total payable (incl. GST)
is **≤ S$1,000**, and only needs: supplier's name, address and GST
registration number; invoice date; an identifying number; description of
goods/services; total amount payable including GST; and a statement
equivalent to "price payable includes GST" (no customer name/address
required).

Source: [IRAS — Invoicing Customers](https://www.iras.gov.sg/taxes/goods-services-tax-(gst)/basics-of-gst/invoicing-price-display-and-record-keeping/invoicing-customers).
This project's generated invoices model the regular (>$1,000) format
throughout, since every vendor transaction in the dataset clears that
threshold; the simplified-invoice field set is not separately modeled.

### B8. GST rounding

**Confidence: Primary**, confirmed by two independent web searches
returning the identical worked example (GST of $1.145 rounds up to $1.15;
$1.144 rounds down to $1.14 — a specific enough figure that independent
fabrication by two separate search passes is implausible).

IRAS permits rounding the GST amount shown on a tax invoice to the nearest
whole cent. IRAS guidance also accepts computing GST either per line item
and summing, or once on the GST-exclusive invoice total, as long as the
method is applied consistently. Source: IRAS e-Tax Guides "GST: General
Guide for Businesses" and "GST: Guide For Retailers" (iras.gov.sg,
`/media/docs/default-source/e-tax/...`).

**This project's method**: GST-exclusive subtotal computed once (sum of
line amounts, each already rounded to 2dp), GST computed once on that
subtotal (`ROUND_HALF_UP`, 2dp) — the "once on the invoice total" method
IRAS explicitly accepts. See `src/invoice_agent/money.py`.

### B9. GST registration number / UEN format

**Confidence: No primary source found** — flagged explicitly rather than
guessed, per instruction. Secondary sources converge on this structure:
businesses registered before 2009 use 8 digits + a check letter (e.g.
`12345678X`); local companies registered 2009+ use a 4-digit year + 5
digits + check letter (e.g. `201812345K`); other entities (LLPs, societies,
etc.) use a century letter + 2-digit year + 2-letter entity code + 4 digits
+ check letter (e.g. `T09LL1234A`). No ACRA- or IRAS-hosted page describing
either the format or a check-digit algorithm was found; multiple
independent secondary sources state the check-digit formula has never been
published by ACRA. Sources (all secondary):
[osome.com](https://osome.com/sg/guides/uen-singapore/),
[commenda.io](https://www.commenda.io/blog/singapore-uen-verification).

**Consequence for this project**: GST registration numbers are validated
by **format only** (digit/letter pattern per the structures above) — no
check-digit validation is implemented, since no authoritative algorithm
could be sourced. This is stated here and in the validation engine code,
not silently assumed.

## C. Singapore e-invoicing (InvoiceNow / Peppol)

**InvoiceNow** is Singapore's national e-invoicing network, built on
**Peppol** (international interoperability framework for structured
e-documents); IMDA became the first Peppol Authority outside Europe in
2018. The technical format is **SG Peppol BIS Billing 3.0**, a
Singapore-localized profile of the international Peppol BIS Billing 3.0
standard (itself based on EN 16931), using GST terminology instead of EU
VAT. IRAS is progressively mandating InvoiceNow submission for
GST-registered businesses, phased through 2028–2031.

**Confidence: Primary** for the BIS Billing 3.0 structure
([docs.peppol.eu — ubl:Invoice](https://docs.peppol.eu/poacc/billing/3.0/syntax/ubl-invoice/),
maintained by OpenPeppol, the standard's governing body) and for the IRAS
mandate ([IRAS — GST InvoiceNow Requirement](https://www.iras.gov.sg/taxes/goods-services-tax-(gst)/gst-invoicenow-requirement)).
Mandatory top-level elements referenced when building this project's
sample e-invoice JSON: `CustomizationID`/`ProfileID`, invoice `ID`,
`IssueDate`, `InvoiceTypeCode`, `DocumentCurrencyCode`,
`AccountingSupplierParty`/`AccountingCustomerParty` (with GST reg. no.),
`TaxTotal`, `LegalMonetaryTotal`, and `InvoiceLine` entries.

**This project's e-invoice samples are a simplified, non-spec-compliant
illustration** of that structure (plain nested JSON mirroring the UBL
field names, not a validated Peppol XML payload) — stated explicitly so
nobody mistakes `data/invoices/*.einvoice.json` for a certified Peppol
document.
