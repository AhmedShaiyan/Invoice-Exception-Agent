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

ALL_CODES = [
    PO_NOT_FOUND, VENDOR_NOT_FOUND, VENDOR_BLOCKED, PRICE_VARIANCE,
    PRICE_UNIT_MISMATCH, QTY_EXCEEDS_GR, UOM_MISMATCH, LINE_NOT_ON_PO,
    GST_RATE_INVALID, GST_REG_MISMATCH, TOTAL_MISMATCH, CURRENCY_MISMATCH,
    DUPLICATE_INVOICE, NEAR_DUPLICATE_INVOICE, CREDIT_NOTE_UNSUPPORTED,
]

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
}
