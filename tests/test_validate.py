"""Tests for the deterministic validation engine.

Two kinds of test here, deliberately kept separate:

1. `test_full_dataset_matches_ground_truth` -- the real proof. Feeds all 217
   dataset invoices (ground_truth.json) through the validator in
   chronological order (see validate_batch / docs/design_decisions.md) and
   requires an exact decision + exception-code match against hand-authored
   ground truth. Per CLAUDE.md: "Any miss is a bug" -- this test has no
   tolerance for partial credit, by design.

2. Hand-crafted unit tests below it, each isolating one rule (tolerance
   boundary, price-unit confusion, line matching without a PO item number,
   GST rounding tolerance, sequential duplicate/quantity state) against a
   known, stable fixture line from the generated SAP data. These exist
   because ground_truth.json's "fields" always carries a real po_item (it's
   the true underlying data) -- the description-similarity line-matching
   fallback is only exercised once real extraction sometimes can't read a
   po_item off a keyless layout (section 3+), so it needs its own direct
   test here to prove the fallback logic itself is correct independent of
   that.
"""
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from invoice_agent import codes as C  # noqa: E402
from invoice_agent.ledger import Ledger  # noqa: E402
from invoice_agent.sap_data import SapData  # noqa: E402
from invoice_agent.validate import Validator, validate_batch  # noqa: E402

SAP_DIR = ROOT / "data" / "sap"
GROUND_TRUTH_PATH = ROOT / "data" / "invoices" / "ground_truth.json"


@pytest.fixture
def validator():
    """Fresh ledger per test -- synthetic unit tests below must not see
    state left over by the full-dataset test or by each other."""
    return Validator(SapData.from_csvs(SAP_DIR), Ledger.from_csvs(SAP_DIR))


def base_invoice(**overrides):
    """A minimal, valid invoice skeleton (vendor 100001, PO 4500012001 line
    00010 -- A4 Copier Paper, fully received, 199 units available, netpr
    6.80, peinh 1 -- confirmed stable against the current generated SAP
    data). Tests override only what they're testing."""
    fields = {
        "invoice_number": "TEST-0001", "invoice_date": "2026-09-01",
        "vendor_name": "Lion City Office Supplies Pte Ltd",
        "vendor_gst_reg_no": "201812345K", "po_number": "4500012001",
        "currency": "SGD",
        "lines": [{"po_item": "00010", "po_number": "4500012001", "material": "MAT-1001",
                   "description": "A4 Copier Paper (500 sheets)", "quantity": 10,
                   "unit": "BOX", "unit_price": "6.80", "amount": "68.00"}],
        "subtotal": "68.00", "gst_rate": "0.09", "gst_amount": "6.12", "total": "74.12",
    }
    fields.update(overrides)
    return fields


# ================================================= match_lines (display helper)
def test_match_lines_resolves_same_po_item_as_validate(validator):
    """match_lines is used by the Invoice Review page to build a match
    table -- it must resolve the same PO line validate() itself used, or a
    reviewer would see a match that disagrees with the actual decision."""
    fields = base_invoice()
    result = validator.validate(fields, post=False)
    [display_match] = validator.match_lines(fields)
    assert display_match.matched is True
    assert display_match.ekpo == result.line_results[0].ekpo


def test_match_lines_does_not_consume_ledger_quantity(validator):
    """Read-only by design: calling it must not affect a later validate()
    call's QTY_EXCEEDS_GR check, unlike validate() itself (post=True)."""
    fields = base_invoice()
    before = validator.ledger.remaining_qty("4500012001", "00010")
    validator.match_lines(fields)
    validator.match_lines(fields)
    after = validator.ledger.remaining_qty("4500012001", "00010")
    assert before == after


# ===================================================== PO-number normalization
@pytest.mark.parametrize("prefix", ["PO ", "PO#", "PO:", "P.O. ", "po "])
def test_po_prefix_on_header_and_line_is_stripped_before_lookup(validator, prefix):
    """Regression test for a real false rejection caught live in section 6's
    eval run: the "minimal" layout literally prints "Order Ref : PO
    4500012170" -- extraction faithfully transcribes that as po_number="PO
    4500012170" (correct extraction behaviour per extract.py's system
    prompt), which must still resolve to the real PO 4500012170, not
    PO_NOT_FOUND."""
    fields = base_invoice(po_number=f"{prefix}4500012001",
                           lines=[dict(base_invoice()["lines"][0], po_number=f"{prefix}4500012001")])
    result = validator.validate(fields)
    assert result.decision == "AUTO_APPROVE"
    assert result.exception_codes == []


def test_po_number_without_prefix_still_works(validator):
    """The common case -- most layouts never print a prefix at all -- must
    be unaffected by the normalization."""
    result = validator.validate(base_invoice())
    assert result.decision == "AUTO_APPROVE"


# ============================================= real-extraction-shaped lines
def _without_line_po_number(fields):
    """extract.py's ExtractedLine schema has no per-line po_number field --
    only ground_truth.json's synthetic fixtures (and base_invoice() above)
    carry one. Strip it to simulate what the validator actually receives
    from a real pipeline run."""
    return {**fields, "lines": [{k: v for k, v in ln.items() if k != "po_number"}
                                 for ln in fields["lines"]]}


def test_cumulative_qty_tracked_without_per_line_po_number(validator):
    """Regression test for the real false approval caught live in section
    6's eval run (INV_052_KAL-2607-1189.pdf, sequential_partial_2_of_2):
    Ledger.record_invoice silently credited nothing against any PO line
    because it only trusted a per-line po_number that real extraction never
    provides, so a cumulative-quantity overshoot across two invoices was
    never detected. Two invoices against the same line (28 then 31 of a PO
    ordered for 199, all received) individually fit, but PO 4500012001's
    line 00010 only has 199 - 170ish headroom in this fixture, so push the
    combined total past what's actually been received."""
    first = _without_line_po_number(base_invoice(invoice_number="SEQ-0001",
                                                  lines=[{**base_invoice()["lines"][0], "quantity": 100,
                                                          "amount": "680.00"}],
                                                  subtotal="680.00", gst_amount="61.20", total="741.20"))
    second = _without_line_po_number(base_invoice(invoice_number="SEQ-0002",
                                                   lines=[{**base_invoice()["lines"][0], "quantity": 150,
                                                           "amount": "1020.00"}],
                                                   subtotal="1020.00", gst_amount="91.80", total="1111.80"))
    r1 = validator.validate(first)
    assert r1.decision == "AUTO_APPROVE"   # 100 of 199 available -- fits on its own
    r2 = validator.validate(second)
    # 100 + 150 = 250 > 199 received -- must be caught on the second invoice
    assert r2.decision == "EXCEPTION"
    assert C.QTY_EXCEEDS_GR in r2.exception_codes


def test_multi_po_line_disambiguated_by_material_when_po_item_collides(validator):
    """Two different POs can legitimately both use item number "00010".
    Without a per-line po_number, po_item alone is ambiguous across POs --
    must not silently guess one (which would credit the wrong PO's
    quantity ledger) and must still resolve correctly via material/
    description. Mirrors the real multi_po_clean regression this fix
    caused and then fixed (docs/decisions_log_full.md section 6)."""
    fields = base_invoice(
        po_number="4500012001/4500012002",
        lines=[
            {"po_item": "00010", "material": "MAT-1001", "description": "A4 Copier Paper (500 sheets)",
             "quantity": 5, "unit": "BOX", "unit_price": "6.80", "amount": "34.00"},
        ],
        subtotal="34.00", gst_amount="3.06", total="37.06",
    )
    fields = _without_line_po_number(fields)
    result = validator.validate(fields)
    assert result.line_results[0].matched is True
    assert result.line_results[0].match_method in ("material", "description")


def test_multi_po_line_unmatched_when_material_and_description_also_collide(validator):
    """The real regression caught live in section 6's eval
    (INV_130_JUR-2609-1070.pdf, INV_154_MAR-2609-1077.pdf): a vendor can
    legitimately reorder the *same* material via two different POs in one
    invoice, so po_item, material, AND description all collide across the
    two valid POs -- there is no signal left in the line itself to pick
    the right one. PO 4500012001 and 4500012037 both carry MAT-1001 ("A4
    Copier Paper (500 sheets)") at item 00010 for this vendor (confirmed
    against the real generated data). Before this fix, the material/
    description fallback silently returned the first candidate found,
    crediting one PO's quantity ledger with both lines' quantities and
    corrupting it for both referenced POs -- not a false approval every
    time (sometimes capacity happened not to be exceeded), but wrong
    bookkeeping regardless. Must now refuse to guess (LINE_NOT_ON_PO)
    rather than silently pick one. See docs/decisions_log_full.md section 6."""
    fields = base_invoice(
        po_number="4500012001/4500012037",
        lines=[
            {"po_item": "00010", "material": "MAT-1001", "description": "A4 Copier Paper (500 sheets)",
             "quantity": 5, "unit": "BOX", "unit_price": "6.80", "amount": "34.00"},
        ],
        subtotal="34.00", gst_amount="3.06", total="37.06",
    )
    fields = _without_line_po_number(fields)
    result = validator.validate(fields)
    assert result.line_results[0].matched is False
    assert result.decision == "EXCEPTION"
    assert C.LINE_NOT_ON_PO in result.exception_codes


# ============================================================ full dataset
def test_full_dataset_matches_ground_truth():
    ground_truth = json.loads(GROUND_TRUTH_PATH.read_text())
    validator = Validator.from_csvs(SAP_DIR)
    results = validate_batch(validator, ground_truth)

    mismatches = []
    for entry, result in results:
        expected_decision = entry["expected_decision"]
        expected_codes = sorted(entry["expected_exceptions"])
        if result.decision != expected_decision or result.exception_codes != expected_codes:
            mismatches.append(
                f"{entry['file']} ({entry['scenario']}): "
                f"expected {expected_decision} {expected_codes}, "
                f"got {result.decision} {result.exception_codes}"
            )

    if mismatches:
        pytest.fail(f"{len(mismatches)}/{len(ground_truth)} invoices mismatched:\n" + "\n".join(mismatches))


def test_zero_false_approvals():
    """Restates the headline eval requirement as a hard gate: no invoice
    that should be an EXCEPTION may come back AUTO_APPROVE. Kept separate
    from the full-match test so a false-approval regression (the costly
    direction of error) fails loudly under its own name, not buried in a
    generic mismatch count."""
    ground_truth = json.loads(GROUND_TRUTH_PATH.read_text())
    validator = Validator.from_csvs(SAP_DIR)
    results = validate_batch(validator, ground_truth)
    false_approvals = [entry["file"] for entry, result in results
                        if entry["expected_decision"] == "EXCEPTION" and result.decision == "AUTO_APPROVE"]
    assert false_approvals == [], f"False approvals: {false_approvals}"


# ======================================================= price tolerance
# PO 4500012001 line 00010's true unit price is a flat $6.80, so +/-1% of it
# (6.80 * 0.01 = 0.068) isn't itself a whole number of cents -- a *dataset*
# invoice (always 2dp) can never land exactly on this boundary, so
# generate_invoices.py's tolerance_boundary scenario necessarily tests
# "just inside" / "just outside" rather than the literal mathematical edge.
# This unit test isolates the validator's comparison logic itself (which
# operates on exact Decimal arithmetic, not 2dp-quantized input) by feeding
# it the literal boundary value directly -- an early version of this test
# quantized to cents first, which silently tested "+1.03%" while believing
# it was testing "+1.00%", and failed for the wrong reason.
TRUE_PRICE = Decimal("6.80")
ONE_PCT = TRUE_PRICE * Decimal("0.01")   # exact: 0.068


@pytest.mark.parametrize("price,should_pass", [
    (TRUE_PRICE, True),                 # exact match
    (TRUE_PRICE + ONE_PCT, True),       # exactly +1% -- boundary is inclusive
    (TRUE_PRICE + ONE_PCT + Decimal("0.001"), False),   # a hair over +1%
    (TRUE_PRICE - ONE_PCT, True),       # exactly -1%
    (TRUE_PRICE - ONE_PCT - Decimal("0.001"), False),   # a hair under -1%
])
def test_price_tolerance_boundary(validator, price, should_pass):
    amount = price * 10
    inv = base_invoice(lines=[{"po_item": "00010", "po_number": "4500012001", "material": "MAT-1001",
                                "description": "A4 Copier Paper (500 sheets)", "quantity": 10,
                                "unit": "BOX", "unit_price": str(price), "amount": str(amount)}],
                        subtotal=str(amount), gst_amount=str((amount * Decimal("0.09")).quantize(Decimal("0.01"))),
                        total=str((amount * Decimal("1.09")).quantize(Decimal("0.01"))))
    result = validator.validate(inv)
    if should_pass:
        assert C.PRICE_VARIANCE not in result.exception_codes
    else:
        assert C.PRICE_VARIANCE in result.exception_codes


# =================================================== price-unit confusion
def test_price_unit_mismatch_detected_not_generic_price_variance(validator):
    """PO 4500012002 line 00010: netpr=3.80, peinh=100 (price is per 100
    units). Billing 3.80 directly as a per-unit price is the classic
    "forgot to divide by peinh" mistake -- must be flagged specifically as
    PRICE_UNIT_MISMATCH, not folded into generic PRICE_VARIANCE, since the
    root cause (and the fix) is completely different."""
    inv = base_invoice(
        vendor_name="Merlion Data Systems Pte Ltd", vendor_gst_reg_no="201923456M",
        po_number="4500012002",
        lines=[{"po_item": "00010", "po_number": "4500012002", "material": "MAT-2004",
                "description": "Cat6 Patch Cable 0.5m", "quantity": 10,
                "unit": "EA", "unit_price": "3.80", "amount": "38.00"}],
        subtotal="38.00", gst_amount="3.42", total="41.42",
    )
    result = validator.validate(inv)
    assert C.PRICE_UNIT_MISMATCH in result.exception_codes
    assert C.PRICE_VARIANCE not in result.exception_codes


# ============================================================= line matching
def test_line_matches_by_description_when_po_item_missing(validator):
    """Simulates what a `minimal`-layout extraction would hand the
    validator: no po_item, no material code, just description/qty/price --
    see docs/design_decisions.md "Line matching for the minimal layout"."""
    inv = base_invoice(lines=[{"po_item": "", "po_number": "4500012001", "material": "",
                                "description": "A4 Copier Paper (500 sheets)", "quantity": 10,
                                "unit": "BOX", "unit_price": "6.80", "amount": "68.00"}])
    result = validator.validate(inv)
    assert result.line_results[0].matched is True
    assert result.line_results[0].match_method == "description"
    assert C.LINE_NOT_ON_PO not in result.exception_codes


def test_line_with_no_plausible_match_routes_to_review(validator):
    """A line that doesn't resemble anything on the referenced PO (wrong
    description, implausible price) must not be guessed at -- see
    docs/design_decisions.md on why silently matching is worse than
    routing to review."""
    inv = base_invoice(lines=[{"po_item": "", "po_number": "4500012001", "material": "",
                                "description": "Completely Unrelated Service Item", "quantity": 1,
                                "unit": "EA", "unit_price": "9999.00", "amount": "9999.00"}])
    result = validator.validate(inv)
    assert result.line_results[0].matched is False
    assert C.LINE_NOT_ON_PO in result.exception_codes


# ================================================================ GST rounding
def test_gst_rounding_tolerance_accepts_alternate_method(validator):
    """A 1-cent GST difference from the alternate IRAS-accepted rounding
    method (docs/data_sources.md B8) must not trip TOTAL_MISMATCH, as long
    as subtotal + stated gst = stated total."""
    inv = base_invoice(gst_amount="6.13", total="74.13")   # 1 cent above 68.00*9%=6.12
    result = validator.validate(inv)
    assert C.TOTAL_MISMATCH not in result.exception_codes


def test_gst_amount_far_off_rate_is_flagged(validator):
    """Stated rate is 9% but gst_amount doesn't back that up by more than
    rounding slack -- a real inconsistency, not rounding noise."""
    inv = base_invoice(gst_amount="20.00", total="88.00")
    result = validator.validate(inv)
    assert C.TOTAL_MISMATCH in result.exception_codes


def test_arithmetic_error_flagged_regardless_of_gst_tolerance(validator):
    inv = base_invoice(total="150.00")   # doesn't match subtotal + gst_amount at all
    result = validator.validate(inv)
    assert C.TOTAL_MISMATCH in result.exception_codes


# ========================================================== sequential state
def test_duplicate_detected_against_prior_invoice_same_validator(validator):
    """Posting invoice A, then an exact (vendor, invoice_number) resubmission
    B through the *same* validator instance must flag B as a duplicate --
    proves the ledger carries state across validate() calls, which is what
    the whole dataset relies on (see docs/design_decisions.md)."""
    first = base_invoice(invoice_number="DUP-TEST-01", invoice_date="2026-09-01")
    second = base_invoice(invoice_number="DUP-TEST-01", invoice_date="2026-09-03")
    r1 = validator.validate(first)
    r2 = validator.validate(second)
    assert C.DUPLICATE_INVOICE not in r1.exception_codes
    assert C.DUPLICATE_INVOICE in r2.exception_codes


def test_sequential_partial_invoices_cumulative_overshoot(validator):
    """PO 4500012001 line 00010 has exactly 199 units invoiceable. Billing
    120 then 100 (240 total) must pass the first and fail the second on
    QTY_EXCEEDS_GR -- proves the validator checks *cumulative* invoiced
    quantity via the ledger, not each invoice against raw GR independently
    (the specific gap a pre-EKBE version of this project's qty check had)."""
    first = base_invoice(invoice_number="SEQ-TEST-01", invoice_date="2026-09-01",
                          lines=[{"po_item": "00010", "po_number": "4500012001", "material": "MAT-1001",
                                   "description": "A4 Copier Paper (500 sheets)", "quantity": 120,
                                   "unit": "BOX", "unit_price": "6.80", "amount": "816.00"}],
                          subtotal="816.00", gst_amount="73.44", total="889.44")
    second = base_invoice(invoice_number="SEQ-TEST-02", invoice_date="2026-09-05",
                           lines=[{"po_item": "00010", "po_number": "4500012001", "material": "MAT-1001",
                                    "description": "A4 Copier Paper (500 sheets)", "quantity": 100,
                                    "unit": "BOX", "unit_price": "6.80", "amount": "680.00"}],
                           subtotal="680.00", gst_amount="61.20", total="741.20")
    r1 = validator.validate(first)
    r2 = validator.validate(second)
    assert C.QTY_EXCEEDS_GR not in r1.exception_codes
    assert C.QTY_EXCEEDS_GR in r2.exception_codes


# =============================================================== vendor/misc
def test_blocked_vendor_flagged():
    sap = SapData.from_csvs(SAP_DIR)
    blocked_lifnr = next(lifnr for lifnr, v in sap.vendors.items() if v["sperr"] == "X")
    blocked_name = sap.vendors[blocked_lifnr]["name1"]
    validator_ = Validator(sap, Ledger.from_csvs(SAP_DIR))
    inv = base_invoice(vendor_name=blocked_name, vendor_gst_reg_no=sap.vendors[blocked_lifnr]["stcd1"],
                        po_number="4500012001")
    result = validator_.validate(inv)
    assert C.VENDOR_BLOCKED in result.exception_codes


def test_unknown_vendor_short_circuits(validator):
    inv = base_invoice(vendor_name="Totally Fictional Pte Ltd", vendor_gst_reg_no="209999999Z")
    result = validator.validate(inv)
    assert result.decision == "EXCEPTION"
    assert result.exception_codes == [C.VENDOR_NOT_FOUND]


def test_credit_note_short_circuits(validator):
    inv = base_invoice(total="-74.12",
                        lines=[{"po_item": "00010", "po_number": "4500012001", "material": "MAT-1001",
                                "description": "A4 Copier Paper (500 sheets)", "quantity": -10,
                                "unit": "BOX", "unit_price": "6.80", "amount": "-68.00"}])
    result = validator.validate(inv)
    assert result.exception_codes == [C.CREDIT_NOTE_UNSUPPORTED]
