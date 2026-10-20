"""Plain-English rendering of the raw JSON this project stores for audit
purposes (ap.exceptions.detail, ap.audit_log tool-call payloads). The raw
JSON stays exactly as persisted -- nothing here changes what's stored or
decided, it only changes what a human reviewer is shown. Used by the
Invoice Review page's match table / findings card and the Audit Trail
page, so the two can't describe the same event two different ways.
"""


def format_detail_sentences(code: str, detail) -> list:
    """detail is the list of dicts validate.py's flag() accumulated for
    this code (one entry per occurrence -- usually one, sometimes more for
    a multi-line issue). Returns one plain sentence per occurrence."""
    if not detail:
        return []
    out = []
    for d in detail:
        out.append(_SENTENCE_BUILDERS.get(code, _fallback)(d))
    return out


def _fallback(d: dict) -> str:
    return "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in d.items())


def _credit_note(d):
    return f"Document total is {d.get('total')}, read as a credit note (negative amount or quantity)."


def _vendor_not_found(d):
    return f"\"{d.get('vendor_name')}\" doesn't match any vendor on file."


def _vendor_blocked(d):
    return f"{d.get('vendor_name')} ({d.get('lifnr')}) is centrally blocked from receiving payment."


def _gst_reg_mismatch(d):
    return (f"Invoice shows GST number {d.get('invoice_gst_reg')}, but the vendor record "
            f"has {d.get('vendor_master_gst_reg')}.")


def _po_not_found(d):
    return f"PO {d.get('po_number')} doesn't exist in the purchasing system."


def _currency_mismatch(d):
    return (f"Invoice is in {d.get('invoice_currency')}, but PO {d.get('po_number')} "
            f"was raised in {d.get('po_currency')}.")


def _gst_rate_invalid(d):
    return f"Invoice charges GST at {d.get('invoice_rate')}; the standard rate is {d.get('expected_rate')}."


def _total_mismatch(d):
    if "subtotal_plus_gst" in d:
        return f"Stated total is {d.get('stated_total')}, but subtotal + GST comes to {d.get('subtotal_plus_gst')}."
    return f"Stated GST is {d.get('stated_gst')}, expected about {d.get('recomputed_gst')} for this rate."


def _line_not_on_po(d):
    return f"Line \"{d.get('description')}\" has no matching item on the referenced PO."


def _price_unit_mismatch(d):
    return (f"Billed at {d.get('invoiced_price')}/unit, but the PO's price ({d.get('po_netpr')}) "
            f"is per {d.get('po_peinh')} units -- true unit price is {d.get('true_unit_price')}.")


def _price_variance(d):
    pct = d.get("variance_pct")
    return (f"Billed at {d.get('invoiced_price')}, PO price is {d.get('po_unit_price')}"
            + (f" ({pct}% off)." if pct else "."))


def _uom_mismatch(d):
    return f"Invoiced in {d.get('invoiced_unit')}, PO line is in {d.get('po_unit')}."


def _qty_exceeds_gr(d):
    return f"Invoiced {d.get('invoiced_qty')} units, but only {d.get('remaining_invoiceable_qty')} remain invoiceable."


def _duplicate(d):
    return f"Matches invoice {d.get('matched_invoice')} already posted on {d.get('matched_date')}."


def _near_duplicate(d):
    return (f"Looks like invoice {d.get('matched_invoice')} (posted {d.get('matched_date')}), "
            f"just reformatted -- {d.get('similarity')}% similar.")


def _extraction_issue(d):
    conf = d.get("confidence")
    base = d.get("error") or "Could not confidently read this document."
    return f"{base}" + (f" (confidence: {conf})" if conf else "")


_SENTENCE_BUILDERS = {
    "CREDIT_NOTE_UNSUPPORTED": _credit_note,
    "VENDOR_NOT_FOUND": _vendor_not_found,
    "VENDOR_BLOCKED": _vendor_blocked,
    "GST_REG_MISMATCH": _gst_reg_mismatch,
    "PO_NOT_FOUND": _po_not_found,
    "CURRENCY_MISMATCH": _currency_mismatch,
    "GST_RATE_INVALID": _gst_rate_invalid,
    "TOTAL_MISMATCH": _total_mismatch,
    "LINE_NOT_ON_PO": _line_not_on_po,
    "PRICE_UNIT_MISMATCH": _price_unit_mismatch,
    "PRICE_VARIANCE": _price_variance,
    "UOM_MISMATCH": _uom_mismatch,
    "QTY_EXCEEDS_GR": _qty_exceeds_gr,
    "DUPLICATE_INVOICE": _duplicate,
    "NEAR_DUPLICATE_INVOICE": _near_duplicate,
    "EXTRACTION_FAILED": _extraction_issue,
    "LOW_CONFIDENCE_EXTRACTION": _extraction_issue,
}


def format_audit_entry(action: str, payload: dict) -> str:
    """One plain sentence summarizing an ap.audit_log entry's payload, for
    the Audit Trail page -- the raw payload stays exactly as persisted
    (append-only, never edited), this only changes what's shown."""
    payload = payload or {}
    builder = _AUDIT_BUILDERS.get(action, _fallback)
    return builder(payload)


def _audit_extract(p):
    conf = p.get("confidence")
    status = p.get("status", "?")
    attempts = p.get("attempts")
    extra = f", {attempts} attempt(s)" if attempts else ""
    return f"Extracted fields -- status: {status}{extra}." + (f" Confidence: {conf}." if conf else "")


def _audit_validate(p):
    decision = p.get("decision", "?")
    codes = p.get("exception_codes") or []
    return f"Decision: {decision}." + (f" Exceptions raised: {', '.join(codes)}." if codes else "")


def _audit_investigate(p):
    return p.get("explanation") or "(no explanation recorded)"


def _audit_revalidate(p):
    return _fallback(p) if p else "Re-ran deterministic validation after a code fix."


def _audit_decision(p):
    reason = p.get("reason")
    return f"Reason: {reason}" if reason else "(no reason recorded)"


def _audit_draft(p):
    return f"Drafted a vendor email: \"{p.get('subject', '(no subject)')}\""


def _audit_sent(p):
    return f"Sent to vendor: \"{p.get('subject', '(no subject)')}\""


_AUDIT_BUILDERS = {
    "extract": _audit_extract,
    "validate": _audit_validate,
    "investigate": _audit_investigate,
    "revalidate": _audit_revalidate,
    "approved": _audit_decision,
    "rejected": _audit_decision,
    "held": _audit_decision,
    "draft_vendor_query": _audit_draft,
    "vendor_query_sent": _audit_sent,
}


def format_tool_call(call: dict) -> str:
    """One readable line for a single {'tool_name', 'input', 'output'}
    entry from an investigation's tool_calls log -- key:value pairs
    instead of a raw JSON blob, still exactly the data that was returned,
    just not brace-and-quote formatted."""
    name = call.get("tool_name", "?")
    inp = call.get("input")
    inp = inp if inp is not None else {}
    out = call.get("output")
    out = out if out is not None else {}
    inp_str = ", ".join(f"{k}={v}" for k, v in inp.items()) if isinstance(inp, dict) else str(inp)
    if isinstance(out, list):
        out_str = f"{len(out)} result(s)" if out else "no results"
    elif isinstance(out, dict):
        out_str = "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in out.items())
    else:
        out_str = str(out)
    return f"**{name}**({inp_str}) -- {out_str}"
