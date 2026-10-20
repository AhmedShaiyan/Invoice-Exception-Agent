"""Canonical exception code list. Single source of truth for both the
dataset generator (section 1, labels ground truth) and the validation
engine (section 2, must emit exactly these codes) -- importing the same
constants means a typo can't silently desync the two."""

PO_NOT_FOUND = "PO_NOT_FOUND"
VENDOR_NOT_FOUND = "VENDOR_NOT_FOUND"
VENDOR_BLOCKED = "VENDOR_BLOCKED"
PRICE_VARIANCE = "PRICE_VARIANCE"
PRICE_UNIT_MISMATCH = "PRICE_UNIT_MISMATCH"
QTY_EXCEEDS_GR = "QTY_EXCEEDS_GR"
UOM_MISMATCH = "UOM_MISMATCH"
LINE_NOT_ON_PO = "LINE_NOT_ON_PO"
GST_RATE_INVALID = "GST_RATE_INVALID"
GST_REG_MISMATCH = "GST_REG_MISMATCH"
TOTAL_MISMATCH = "TOTAL_MISMATCH"
CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
DUPLICATE_INVOICE = "DUPLICATE_INVOICE"
NEAR_DUPLICATE_INVOICE = "NEAR_DUPLICATE_INVOICE"
CREDIT_NOTE_UNSUPPORTED = "CREDIT_NOTE_UNSUPPORTED"
EXTRACTION_FAILED = "EXTRACTION_FAILED"
LOW_CONFIDENCE_EXTRACTION = "LOW_CONFIDENCE_EXTRACTION"

ALL_CODES = [
    PO_NOT_FOUND, VENDOR_NOT_FOUND, VENDOR_BLOCKED, PRICE_VARIANCE,
    PRICE_UNIT_MISMATCH, QTY_EXCEEDS_GR, UOM_MISMATCH, LINE_NOT_ON_PO,
    GST_RATE_INVALID, GST_REG_MISMATCH, TOTAL_MISMATCH, CURRENCY_MISMATCH,
    DUPLICATE_INVOICE, NEAR_DUPLICATE_INVOICE, CREDIT_NOTE_UNSUPPORTED,
    EXTRACTION_FAILED, LOW_CONFIDENCE_EXTRACTION,
]

# Financial-risk severity, used for ap.exceptions.severity and queue
# triage. A judgment call documented in docs/decisions_log_full.md section 5 --
# roughly: HIGH = money could move wrongly or a real compliance/fraud
# signal; MEDIUM = needs a human to resolve but isn't itself a sign the
# invoice is actively wrong; LOW = narrow, usually-cosmetic mismatches.
SEVERITY = {
    DUPLICATE_INVOICE: "HIGH", VENDOR_BLOCKED: "HIGH", CREDIT_NOTE_UNSUPPORTED: "HIGH",
    PRICE_VARIANCE: "HIGH", PRICE_UNIT_MISMATCH: "HIGH", TOTAL_MISMATCH: "HIGH",
    CURRENCY_MISMATCH: "HIGH",
    QTY_EXCEEDS_GR: "MEDIUM", GST_REG_MISMATCH: "MEDIUM", NEAR_DUPLICATE_INVOICE: "MEDIUM",
    PO_NOT_FOUND: "MEDIUM", VENDOR_NOT_FOUND: "MEDIUM",
    EXTRACTION_FAILED: "MEDIUM", LOW_CONFIDENCE_EXTRACTION: "MEDIUM",
    UOM_MISMATCH: "LOW", LINE_NOT_ON_PO: "LOW", GST_RATE_INVALID: "LOW",
}

PLAIN_LABELS = {
    PO_NOT_FOUND: "PO number not found",
    VENDOR_NOT_FOUND: "Vendor not on file",
    VENDOR_BLOCKED: "Vendor is blocked",
    PRICE_VARIANCE: "Price doesn't match the PO",
    PRICE_UNIT_MISMATCH: "Billed at the wrong price unit",
    QTY_EXCEEDS_GR: "Billed more than received",
    UOM_MISMATCH: "Unit of measure doesn't match",
    LINE_NOT_ON_PO: "Line item not on the PO",
    GST_RATE_INVALID: "GST rate is wrong",
    GST_REG_MISMATCH: "GST number doesn't match vendor record",
    TOTAL_MISMATCH: "Total doesn't add up",
    CURRENCY_MISMATCH: "Currency doesn't match the PO",
    DUPLICATE_INVOICE: "Already posted (duplicate)",
    NEAR_DUPLICATE_INVOICE: "Likely duplicate (similar invoice #)",
    CREDIT_NOTE_UNSUPPORTED: "Credit note needs manual handling",
    EXTRACTION_FAILED: "Couldn't read the document",
    LOW_CONFIDENCE_EXTRACTION: "Low-confidence reading of the document",
}

DESCRIPTIONS = {
    PO_NOT_FOUND: "Invoice references a PO number that doesn't exist in EKKO.",
    VENDOR_NOT_FOUND: "Invoice's claimed vendor doesn't match any vendor in LFA1.",
    VENDOR_BLOCKED: "Vendor exists but LFA1.sperr is set -- central posting block.",
    PRICE_VARIANCE: "Unit price outside +/-1% of PO net price (simplification of SAP tolerance key PP / OMR6).",
    PRICE_UNIT_MISMATCH: "Invoice price matches the PO's NETPR figure directly, ignoring PEINH -- the classic 'billed per-unit instead of per-100' error.",
    QTY_EXCEEDS_GR: "Invoiced qty exceeds remaining invoiceable qty (cumulative GR minus already-invoiced, from EKBE).",
    UOM_MISMATCH: "Invoice line's unit of measure doesn't match the PO line's.",
    LINE_NOT_ON_PO: "Invoice line has no corresponding PO line (e.g. freight added without a PO line for it).",
    GST_RATE_INVALID: "GST rate on the invoice isn't the current standard rate (9%).",
    GST_REG_MISMATCH: "Invoice's GST registration number doesn't match LFA1.stcd1 for the matched vendor.",
    TOTAL_MISMATCH: "Subtotal + GST != total, beyond the documented rounding tolerance.",
    CURRENCY_MISMATCH: "Invoice currency doesn't match the PO's currency.",
    DUPLICATE_INVOICE: "Same vendor + same invoice number already posted (RBKP.xblnr match).",
    NEAR_DUPLICATE_INVOICE: "Same vendor + an invoice number that's a near-match (reformatting, not a new invoice) of one already posted.",
    CREDIT_NOTE_UNSUPPORTED: "Document is a credit note; this pipeline only handles standard invoices, routes to review rather than guessing a netting treatment.",
    EXTRACTION_FAILED: "Claude could not produce schema-valid structured output after retries; routed to review rather than guessing or crashing.",
    LOW_CONFIDENCE_EXTRACTION: "Claude's own self-reported confidence on one or more fields fell below the review threshold.",
}
