"""The deterministic three-way-match + GST validation engine. No LLM
involved anywhere in this module -- every decision here is plain
Python/Decimal arithmetic against SAP master data and the ledger.

Call `Validator.validate(fields)` once per invoice, **in invoice-date
order**, on a single Validator instance (it owns a Ledger that accumulates
state as invoices are posted) -- see docs/design_decisions.md "Sequential,
stateful validation" for why date order matters: duplicate detection and
the cumulative quantity check are both only correct if the engine has
already processed what came before. `validate_batch` makes this harder to
get wrong by doing the sort for you.
"""
import re
from dataclasses import dataclass, field
from decimal import Decimal

from rapidfuzz import fuzz

from . import codes as C
from .ledger import Ledger
from .money import D, round2
from .sap_data import SapData

CURRENT_GST_RATE = D("0.09")
PRICE_TOLERANCE = D("0.01")            # +/-1%, inclusive -- see docs/data_sources.md A1
# A price with PEINH > 1 (price per N units) can have a true per-unit price
# below one cent (e.g. $3.80 per 100 units = $0.038/unit). An invoice can
# only ever display 2dp, so $0.038 necessarily prints as $0.04 -- a 5%
# "variance" from unavoidable rounding, not a real pricing error. 1% of a
# sub-cent true price is itself a fraction of a cent, smaller than the
# rounding granularity that produced the invoice in the first place, so a
# literal 1% comparison always fails on these lines regardless of whether
# the vendor priced it correctly. Flooring the tolerance at half a cent (the
# maximum possible 2dp rounding error) fixes this without loosening the
# check for anything priced high enough for 1% to already exceed it.
PRICE_TOLERANCE_FLOOR = D("0.005")
GST_ROUNDING_TOLERANCE_PER_LINE = D("0.01")
GST_ROUNDING_TOLERANCE_MIN = D("0.02")
DESCRIPTION_MATCH_SCORE = 85           # see docs/design_decisions.md "Line matching"
PRICE_PLAUSIBILITY_BAND = D("0.5")     # sanity check only, not the +/-1% tolerance itself

# Some layouts print the PO number with a leading "PO"/"P.O."/"PO#"/"PO:"
# baked into the value itself (e.g. the "minimal" layout's literal
# "Order Ref : PO 4500012170" -- other layouts keep the printed value clean
# and put "PO" only in a separate label). Extraction faithfully transcribes
# exactly what's printed, which is correct per extract.py's own system
# prompt -- the normalization belongs here, in the deterministic matching
# layer, not in the LLM prompt. Found live during section 6's eval run
# (INV_008_TAN-2607-1826.pdf, a clean invoice spuriously flagged
# PO_NOT_FOUND) -- see docs/decisions_log.md section 6.
_PO_PREFIX_RE = re.compile(r"^\s*p\.?\s*o\.?\s*#?\s*:?\s*", re.IGNORECASE)


def _normalize_po(raw) -> str:
    if not raw:
        return raw
    return _PO_PREFIX_RE.sub("", raw).strip()


@dataclass
class LineResult:
    description: str
    matched: bool
    match_method: str = None           # "po_item" | "material" | "description" | None
    ekpo: dict = None


@dataclass
class ValidationResult:
    decision: str                       # "AUTO_APPROVE" or "EXCEPTION"
    exception_codes: list = field(default_factory=list)   # sorted, deduped
    details: dict = field(default_factory=dict)            # code -> list of detail dicts
    matched_vendor: str = None
    line_results: list = field(default_factory=list)


class Validator:
    def __init__(self, sap: SapData, ledger: Ledger):
        self.sap = sap
        self.ledger = ledger

    @classmethod
    def from_csvs(cls, sap_dir) -> "Validator":
        return cls(SapData.from_csvs(sap_dir), Ledger.from_csvs(sap_dir))

    # ----------------------------------------------------------------- #
    def validate(self, fields: dict, post: bool = True) -> ValidationResult:
        codes = set()
        details = {}

        def flag(code, **detail):
            codes.add(code)
            details.setdefault(code, []).append(detail)

        lines = [dict(l, po_number=_normalize_po(l.get("po_number"))) for l in fields["lines"]]

        # --- credit note: short-circuit, this pipeline doesn't net credit
        # notes against anything, it routes them to a human. Checking first
        # avoids negative amounts tripping unrelated checks in confusing ways.
        if D(fields["total"]) < 0 or any(int(l["quantity"]) < 0 for l in lines):
            flag(C.CREDIT_NOTE_UNSUPPORTED, total=fields["total"])
            return ValidationResult("EXCEPTION", sorted(codes), details)

        # --- vendor resolution (by name -- see docs/decisions_log.md on why
        # this is a simplification: no fuzzy vendor-name matching) ---
        lifnr = self.sap.find_vendor_by_name(fields["vendor_name"])
        if lifnr is None:
            flag(C.VENDOR_NOT_FOUND, vendor_name=fields["vendor_name"])
            return ValidationResult("EXCEPTION", sorted(codes), details)
        vendor = self.sap.vendors[lifnr]

        if vendor["sperr"] == "X":
            flag(C.VENDOR_BLOCKED, lifnr=lifnr, vendor_name=vendor["name1"])

        if fields["vendor_gst_reg_no"] != vendor["stcd1"]:
            flag(C.GST_REG_MISMATCH, invoice_gst_reg=fields["vendor_gst_reg_no"],
                 vendor_master_gst_reg=vendor["stcd1"])

        # --- PO existence + currency, per referenced PO ---
        referenced_pos = sorted({l["po_number"] for l in lines if l.get("po_number")})
        if not referenced_pos:
            referenced_pos = [_normalize_po(p) for p in fields.get("po_number", "").split("/") if p]
        valid_pos = []
        for ebeln in referenced_pos:
            head = self.sap.po_header.get(ebeln)
            if head is None:
                flag(C.PO_NOT_FOUND, po_number=ebeln)
                continue
            valid_pos.append(ebeln)
            if head["waers"] != fields["currency"]:
                flag(C.CURRENCY_MISMATCH, po_number=ebeln, po_currency=head["waers"],
                     invoice_currency=fields["currency"])

        # --- GST rate ---
        if D(fields["gst_rate"]) != CURRENT_GST_RATE:
            flag(C.GST_RATE_INVALID, invoice_rate=fields["gst_rate"],
                 expected_rate=str(CURRENT_GST_RATE))

        # --- arithmetic: subtotal + gst = total, with documented rounding slack
        # on gst only (see docs/data_sources.md B8 -- IRAS accepts either
        # per-line or on-subtotal GST rounding, so a cent or two of GST
        # variance isn't a real error). Two independent checks, deliberately
        # separated:
        #   1. does subtotal + stated gst_amount = stated total? (pure
        #      arithmetic -- this is what arithmetic_error-style invoices break)
        #   2. is the stated gst_amount consistent with subtotal x the
        #      invoice's *own claimed* gst_rate, within rounding slack?
        # (2) deliberately uses the invoice's claimed rate, not the correct
        # 9% -- GST_RATE_INVALID above already flags a wrong rate; reusing
        # 9% here would double-flag every wrong-rate invoice as TOTAL_MISMATCH
        # too, for a reason that has nothing to do with its arithmetic.
        subtotal = D(fields["subtotal"])
        gst_amount = D(fields["gst_amount"])
        claimed_rate = D(fields["gst_rate"])
        recomputed_gst = round2(subtotal * claimed_rate)
        gst_tolerance = max(GST_ROUNDING_TOLERANCE_MIN, GST_ROUNDING_TOLERANCE_PER_LINE * len(lines))
        recomputed_total = round2(subtotal + gst_amount)
        if round2(fields["total"]) != recomputed_total:
            flag(C.TOTAL_MISMATCH, stated_total=fields["total"],
                 subtotal_plus_gst=str(recomputed_total))
        elif abs(gst_amount - recomputed_gst) > gst_tolerance:
            flag(C.TOTAL_MISMATCH, stated_gst=fields["gst_amount"],
                 recomputed_gst=str(recomputed_gst),
                 note="gst_amount inconsistent with subtotal x invoice's own claimed rate")

        # --- line-level matching + checks ---
        # A line whose referenced PO was already flagged PO_NOT_FOUND isn't
        # *also* "not on the PO" -- that's the same problem restated, not a
        # second one. extract.py's schema has no per-line po_number field
        # (several layouts never repeat the PO number per line, only once
        # at header level -- see docs/design_decisions.md "Line matching"),
        # so there's no way to know, line by line, which of several
        # referenced POs a given line belongs to. The only case this can
        # unambiguously catch is "every referenced PO turned out to be
        # invalid" -- then every line is unmatchable by definition, so skip
        # matching entirely rather than also flagging LINE_NOT_ON_PO for the
        # same root cause.
        all_pos_invalid = bool(referenced_pos) and not valid_pos
        line_results = []
        for ln in lines:
            if all_pos_invalid:
                line_results.append(LineResult(ln.get("description"), False))
                continue
            lr = self._match_line(ln, valid_pos)
            line_results.append(lr)
            if not lr.matched:
                flag(C.LINE_NOT_ON_PO, description=ln.get("description"))
                continue
            ekpo = lr.ekpo
            self._check_line(ln, ekpo, flag)

        # --- duplicate / near-duplicate ---
        exact = self.ledger.find_exact_duplicate(lifnr, fields["invoice_number"])
        if exact:
            flag(C.DUPLICATE_INVOICE, matched_invoice=exact["invoice_number"],
                 matched_date=exact["invoice_date"])
        else:
            near = self.ledger.find_near_duplicates(lifnr, fields["invoice_number"])
            if near:
                score, summary = near[0]
                flag(C.NEAR_DUPLICATE_INVOICE, matched_invoice=summary["invoice_number"],
                     matched_date=summary["invoice_date"], similarity=score)

        decision = "AUTO_APPROVE" if not codes else "EXCEPTION"

        # Post to the ledger so later invoices see this one -- unless it's a
        # duplicate, which never actually posts in a real system (see
        # Ledger.record_invoice docstring).
        #
        # Built from line_results' resolved ekpo (ebeln/ebelp), not the raw
        # extracted line's own po_item/po_number: extract.py's schema has no
        # per-line po_number at all, and po_item is frequently empty even
        # when the line matched via material or description. Posting
        # against the raw fields silently posted nothing for most real
        # invoices -- found live in section 6's eval (a sequential-partial
        # overshoot scenario that should have raised QTY_EXCEEDS_GR came
        # back AUTO_APPROVE because the first invoice's quantity was never
        # actually credited to the ledger). See docs/decisions_log.md
        # section 6.
        if post and C.DUPLICATE_INVOICE not in codes:
            posting_lines = [{"po_number": lr.ekpo["ebeln"], "po_item": lr.ekpo["ebelp"],
                               "quantity": ln["quantity"]}
                              for ln, lr in zip(lines, line_results) if lr.matched]
            self.ledger.record_invoice(lifnr, fields["invoice_number"], fields["invoice_date"], posting_lines)

        return ValidationResult(decision, sorted(codes), details, matched_vendor=lifnr,
                                 line_results=line_results)

    # ----------------------------------------------------------------- #
    def _match_line(self, line: dict, valid_pos: list) -> LineResult:
        # extract.py's schema has no per-line po_number field -- only
        # ground_truth.json's synthetic test fixtures carry one (for
        # scoring purposes). Use it directly when present and valid
        # (unambiguous). When absent, as on every real extracted invoice,
        # try po_item against every valid PO -- but only trust the result
        # when exactly one valid PO contains that item number. Two
        # referenced POs can legitimately share an item number (both use
        # "00010" independently), and guessing wrong would credit the
        # wrong PO's quantity ledger, so an ambiguous po_item falls through
        # to the material/description match instead of picking arbitrarily.
        # Found live in section 6's eval -- see docs/decisions_log.md
        # section 6.
        po_number, po_item = _normalize_po(line.get("po_number")), line.get("po_item")
        if po_item:
            if po_number and po_number in valid_pos:
                ekpo = self.sap.po_lines.get((po_number, po_item))
                if ekpo:
                    return LineResult(line.get("description"), True, "po_item", ekpo)
            elif not po_number:
                matches = [ekpo for ebeln in valid_pos
                           for ekpo in [self.sap.po_lines.get((ebeln, po_item))] if ekpo]
                if len(matches) == 1:
                    return LineResult(line.get("description"), True, "po_item", matches[0])

        # Same ambiguity hazard as po_item above, found live in the same
        # eval run: a vendor can legitimately order the identical material
        # (same code, same description) via two different POs in one
        # invoice (multi_po_clean). Matching the first candidate found
        # silently credited the wrong PO's quantity ledger and produced a
        # real false-positive QTY_EXCEEDS_GR (INV_130_JUR-2609-1070.pdf).
        # Both the material and description paths below now require the
        # match to be unambiguous *across distinct POs* before trusting it
        # -- matches within the same PO (e.g. two lines sharing a material
        # code) are unaffected, only a cross-PO tie falls through instead
        # of guessing. See docs/decisions_log.md section 6.
        candidates = [ekpo for ebeln in valid_pos for ekpo in self.sap.po_lines_by_po.get(ebeln, [])]
        material = line.get("material")
        if material:
            matches = [ekpo for ekpo in candidates if ekpo["matnr"] == material]
            if matches and len({ekpo["ebeln"] for ekpo in matches}) == 1:
                return LineResult(line.get("description"), True, "material", matches[0])

        if candidates:
            desc = (line.get("description") or "").strip().lower()
            scored = [(fuzz.ratio(desc, ekpo["txz01"].strip().lower()), ekpo) for ekpo in candidates]
            best_score = max((s for s, _ in scored), default=0)
            if best_score >= DESCRIPTION_MATCH_SCORE:
                top = [ekpo for s, ekpo in scored if s == best_score]
                if len({ekpo["ebeln"] for ekpo in top}) == 1:
                    best = top[0]
                    true_price = D(best["netpr"]) / D(best["peinh"])
                    inv_price = D(line["unit_price"])
                    if true_price > 0 and abs(inv_price - true_price) / true_price <= PRICE_PLAUSIBILITY_BAND:
                        return LineResult(line.get("description"), True, "description", best)

        return LineResult(line.get("description"), False)

    def _check_line(self, line: dict, ekpo: dict, flag) -> None:
        true_price = D(ekpo["netpr"]) / D(ekpo["peinh"])
        inv_price = D(line["unit_price"])
        price_diff = abs(inv_price - true_price)
        tolerance_amount = max(true_price * PRICE_TOLERANCE, PRICE_TOLERANCE_FLOOR)
        if price_diff > tolerance_amount:
            netpr = D(ekpo["netpr"])
            peinh = int(ekpo["peinh"])
            if peinh > 1 and abs(inv_price - netpr) <= netpr * PRICE_TOLERANCE:
                flag(C.PRICE_UNIT_MISMATCH, invoiced_price=line["unit_price"],
                     po_netpr=ekpo["netpr"], po_peinh=ekpo["peinh"], true_unit_price=str(true_price))
            else:
                flag(C.PRICE_VARIANCE, invoiced_price=line["unit_price"], po_unit_price=str(true_price),
                     variance_pct=str(round2(price_diff / true_price * 100)) if true_price else None)

        # Case-insensitive: a real extraction pipeline can transcribe a unit
        # in a different case than SAP's convention (confirmed live -- a
        # scanned invoice's "box" came back lowercase against EKPO's "BOX")
        # without that being a genuine unit-of-measure problem.
        if line.get("unit") and ekpo.get("meins") and line["unit"].upper() != ekpo["meins"].upper():
            flag(C.UOM_MISMATCH, invoiced_unit=line["unit"], po_unit=ekpo["meins"])

        if ekpo.get("is_service") != "X":
            remaining = self.ledger.remaining_qty(ekpo["ebeln"], ekpo["ebelp"])
            qty = D(line["quantity"])
            if qty > remaining:
                flag(C.QTY_EXCEEDS_GR, invoiced_qty=line["quantity"], remaining_invoiceable_qty=str(remaining))


def validate_batch(validator: Validator, entries: list) -> list:
    """Validate a list of ground-truth-style entries (each with a "fields"
    dict and an "invoice_date" reachable at fields["invoice_date"]) in
    chronological order, posting each to the validator's ledger as it goes.
    Returns results in the same order as the (now date-sorted) input.
    """
    ordered = sorted(entries, key=lambda e: e["fields"]["invoice_date"])
    results = []
    for entry in ordered:
        result = validator.validate(entry["fields"])
        results.append((entry, result))
    return results
