"""Tests for the exception-investigation agent.

Two kinds, as with test_extract.py: deterministic tool-dispatch logic
(tested directly, no API calls) and the hard tool-call cap (tested with a
mocked client that always wants another tool call, so the cap is verified
as an actual enforced property -- not just something the live model happened
to respect in manual testing). The real tool-selection/explanation quality
was verified manually against live invoices across several exception types
(GST_REG_MISMATCH, DUPLICATE_INVOICE, VENDOR_BLOCKED, QTY_EXCEEDS_GR with a
genuinely ambiguous GR history) -- see docs/decisions_log_full.md section 4."""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from invoice_agent.agent import (  # noqa: E402
    Investigator, InvestigationResult, VendorQueryDraft, draft_vendor_query, groundedness_check,
)
from invoice_agent.ledger import Ledger  # noqa: E402
from invoice_agent.sap_data import SapData  # noqa: E402

SAP_DIR = ROOT / "data" / "sap"


@pytest.fixture
def investigator():
    sap = SapData.from_csvs(SAP_DIR)
    ledger = Ledger.from_csvs(SAP_DIR)
    return Investigator(sap, ledger, SAP_DIR, client=MagicMock())


# ================================================================= tools
def test_search_pos_by_vendor_known_vendor(investigator):
    """Summary only -- price/qty/unit are deliberately excluded (get full
    detail via get_po_history once a specific line is identified). See the
    tool's own docstring for why: this was the single largest tool payload
    in the loop, resent in full on every subsequent turn."""
    result = investigator._tool_search_pos_by_vendor("Lion City Office Supplies Pte Ltd")
    assert "error" not in result
    assert result["vendor"] == "Lion City Office Supplies Pte Ltd"
    assert len(result["purchase_orders"]) > 0
    first_po = result["purchase_orders"][0]
    assert "ebeln" in first_po and "lines" in first_po
    assert all("ebelp" in ln and "material" in ln and "description" in ln for ln in first_po["lines"])
    assert all("netpr" not in ln and "order_qty" not in ln for ln in first_po["lines"])


def test_search_pos_by_vendor_unknown_vendor_returns_error(investigator):
    result = investigator._tool_search_pos_by_vendor("Totally Fictional Pte Ltd")
    assert "error" in result


def test_get_po_history_known_line(investigator):
    # 4500012001 / 00010 confirmed stable in tests/test_validate.py's fixture:
    # fully received (199 of 199), no historical invoice against it.
    result = investigator._tool_get_po_history("4500012001", "00010")
    assert "error" not in result
    assert result["line"]["material"] == "MAT-1001"
    assert result["cumulative_received_qty"] == "199"
    assert result["cumulative_invoiced_qty"] == "0"
    assert result["remaining_invoiceable_qty"] == "199"
    assert any(e["type"] == "goods_receipt" for e in result["events"])


def test_get_po_history_unknown_line_returns_error(investigator):
    result = investigator._tool_get_po_history("9999999999", "99999")
    assert "error" in result


def test_get_goods_receipts_known_line(investigator):
    result = investigator._tool_get_goods_receipts("4500012001", "00010")
    assert "movements" in result
    assert len(result["movements"]) >= 1
    assert all("document" in m and "quantity" in m for m in result["movements"])


def test_get_goods_receipts_service_line_is_empty_not_an_error(investigator):
    # find a real service line from the SAP data rather than hardcoding one
    service_line = next((ebeln, ln["ebelp"]) for ebeln, ln in investigator.sap.po_lines.items()
                         if ln["is_service"] == "X")
    result = investigator._tool_get_goods_receipts(*service_line)
    assert result["movements"] == []
    assert "note" in result


def test_get_vendor_master_known_vendor(investigator):
    result = investigator._tool_get_vendor_master("Lion City Office Supplies Pte Ltd")
    assert result["lifnr"] == "100001"
    assert result["gst_reg_no"] == "201812345K"
    assert result["blocked"] is False


def test_get_vendor_master_blocked_vendor(investigator):
    blocked_lifnr = next(lifnr for lifnr, v in investigator.sap.vendors.items() if v["sperr"] == "X")
    blocked_name = investigator.sap.vendors[blocked_lifnr]["name1"]
    result = investigator._tool_get_vendor_master(blocked_name)
    assert result["blocked"] is True


def test_find_prior_invoices_from_vendor_known_history(investigator):
    # pick a vendor the historical seed definitely has entries for
    lifnr_with_history = next(lifnr for lifnr in investigator.ledger.by_vendor if investigator.ledger.by_vendor[lifnr])
    name = investigator.sap.vendors[lifnr_with_history]["name1"]
    result = investigator._tool_find_prior_invoices_from_vendor(name, limit=5)
    assert "invoices" in result
    assert len(result["invoices"]) >= 1
    assert all("invoice_number" in inv and "invoice_date" in inv for inv in result["invoices"])


def test_dispatch_unknown_tool_returns_error_not_exception(investigator):
    result = investigator._dispatch("delete_everything", {})
    assert "error" in result


# ========================================================= hard cap on tool calls
def _tool_use_response(tool_name, tool_input, call_id):
    block = SimpleNamespace(type="tool_use", id=call_id, name=tool_name, input=tool_input)
    return SimpleNamespace(stop_reason="tool_use", content=[block])


def test_hard_cap_never_executes_more_than_max_tool_calls(investigator):
    """Mocked client that always wants to call another tool, forever -- the
    real enforcement being tested is that the loop stops dispatching once
    the cap is reached, not that the live model happens to behave."""
    investigator.max_tool_calls = 3
    call_counter = {"n": 0}

    def fake_create(**kwargs):
        call_counter["n"] += 1
        return _tool_use_response("get_vendor_master", {"vendor_name": "Lion City Office Supplies Pte Ltd"},
                                   f"call_{call_counter['n']}")

    fake_report = SimpleNamespace(parsed_output=InvestigationResult(
        root_cause="test", explanation="test explanation", suggested_action="test action", confidence=0.5))

    investigator.client.messages.create = MagicMock(side_effect=fake_create)
    investigator.client.messages.parse = MagicMock(return_value=fake_report)

    outcome = investigator.investigate({"invoice_number": "X"}, ["PRICE_VARIANCE"])

    assert len(outcome.tool_calls) <= investigator.max_tool_calls
    assert outcome.status == "capped"
    # confirms the loop actually stopped calling the API once capped, not just
    # stopped dispatching while still burning requests
    assert call_counter["n"] <= investigator.max_tool_calls + 1


def test_usage_is_summed_across_every_api_call(investigator):
    """Needed for section 6's per-invoice cost accounting -- a single
    investigation can make several tool-use turns plus the final report
    call, and the cost is the sum across all of them, not just the last
    response's usage."""
    investigator.max_tool_calls = 5
    call_counter = {"n": 0}

    def fake_create(**kwargs):
        call_counter["n"] += 1
        if call_counter["n"] == 1:
            resp = _tool_use_response("get_vendor_master", {"vendor_name": "Lion City Office Supplies Pte Ltd"},
                                       "call_1")
        else:
            resp = SimpleNamespace(stop_reason="end_turn", content=[])
        resp.usage = SimpleNamespace(input_tokens=100, output_tokens=20)
        return resp

    fake_report = SimpleNamespace(
        parsed_output=InvestigationResult(root_cause="t", explanation="t", suggested_action="t", confidence=0.5),
        usage=SimpleNamespace(input_tokens=50, output_tokens=10),
    )
    investigator.client.messages.create = MagicMock(side_effect=fake_create)
    investigator.client.messages.parse = MagicMock(return_value=fake_report)

    outcome = investigator.investigate({"invoice_number": "X"}, ["PRICE_VARIANCE"])

    # 2 messages.create calls (tool_use, then end_turn) + 1 messages.parse call
    assert outcome.usage == {"input_tokens": 100 + 100 + 50, "output_tokens": 20 + 20 + 10}


def test_agent_never_exposes_an_approve_or_reject_tool():
    """Structural guarantee, not a behavioral one: the capability simply
    isn't offered, so there's nothing for a prompt-injection or a model
    mistake to invoke."""
    from invoice_agent.agent import TOOLS
    names = {t["name"] for t in TOOLS}
    assert not any("approve" in n or "reject" in n or "decide" in n for n in names)


# ============================================================= groundedness
def test_groundedness_check_flags_fabricated_number():
    explanation = "The vendor's registered GST number is 999999999Z, which is wrong."
    tool_calls = [{"tool_name": "get_vendor_master", "output": {"gst_reg_no": "201812345K"}}]
    result = groundedness_check(explanation, tool_calls)
    assert result["grounded"] is False
    assert "999999999" in "".join(result["ungrounded_numbers"])


def test_groundedness_check_passes_for_cited_tool_output():
    explanation = "The vendor's GST registration on file is 201812345K."
    tool_calls = [{"tool_name": "get_vendor_master", "output": {"gst_reg_no": "201812345K"}}]
    result = groundedness_check(explanation, tool_calls)
    assert result["grounded"] is True


def test_groundedness_check_ignores_trailing_sentence_period():
    """Regression test for a false positive caught live in section 6's eval
    run: "...the invoiced quantity of 4." extracted as "4." (with the
    sentence-ending period attached), which then never matched the
    grounded "4" from a tool's quantity field."""
    explanation = "The goods receipt confirms the invoiced quantity of 4."
    tool_calls = [{"tool_name": "get_po_history", "output": {"cumulative_received_qty": "4"}}]
    result = groundedness_check(explanation, tool_calls)
    assert result["grounded"] is True


def test_groundedness_check_allows_citing_the_invoice_itself():
    """The agent is explicitly allowed to restate what's printed on the
    invoice it was handed -- that's not a tool lookup, but it's not a
    fabrication either. Regression test for the false positives seen in
    manual testing before invoice_fields was added to the evidence set."""
    explanation = "The invoice bills PO 4500012001, item 00010, for 31 units."
    tool_calls = []
    invoice_fields = {"po_number": "4500012001", "lines": [{"po_item": "00010", "quantity": 31}]}
    result = groundedness_check(explanation, tool_calls, invoice_fields)
    assert result["grounded"] is True


def test_groundedness_check_allows_rate_restated_as_percentage():
    """0.09 in evidence (a gst_rate field), "9" in the explanation as a
    plain-English restatement of 9% -- not a verbatim match, but not a
    fabrication either."""
    tool_calls = [{"tool_name": "x", "input": {}, "output": {"gst_rate": "0.09"}}]
    result = groundedness_check("The invoice charges GST at 9%, which is correct.", tool_calls)
    assert result["grounded"] is True
    assert "9" in result["derived_numbers"]


def test_groundedness_check_allows_difference_of_two_grounded_numbers():
    tool_calls = [{"tool_name": "x", "input": {}, "output": {"ordered": "10", "received": "7"}}]
    result = groundedness_check("Ordered 10, received 7, so 3 units are short.", tool_calls)
    assert result["grounded"] is True
    assert "3" in result["derived_numbers"]


def test_groundedness_check_allows_product_of_two_grounded_numbers():
    tool_calls = [{"tool_name": "x", "input": {}, "output": {"qty": "8", "unit_price": "28.00"}}]
    result = groundedness_check("8 units at 28.00 each comes to 224.00.", tool_calls)
    assert result["grounded"] is True
    assert "224.00" in result["derived_numbers"]


def test_groundedness_check_still_flags_a_number_no_rule_explains():
    """The derivation rules must not swallow a genuine fabrication -- a
    number unrelated to any sum/difference/product/rate of the evidence
    still fails."""
    tool_calls = [{"tool_name": "x", "input": {}, "output": {"qty": "8", "unit_price": "28.00"}}]
    result = groundedness_check("The vendor has been late on 47 prior occasions.", tool_calls)
    assert result["grounded"] is False
    assert "47" in result["ungrounded_numbers"]


# ========================================================= draft_vendor_query
def test_draft_vendor_query_calls_parse_not_create_no_tools():
    """A cheap, tool-less follow-up -- must not open a tool-use loop like a
    fresh investigation would."""
    client = MagicMock()
    fake_response = SimpleNamespace(
        parsed_output=VendorQueryDraft(subject="Re: Invoice ABC-123", body="Dear vendor, ..."),
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )
    client.messages.parse = MagicMock(return_value=fake_response)

    result = draft_vendor_query(
        {"invoice_number": "ABC-123", "vendor_name": "Test Vendor"}, ["QTY_EXCEEDS_GR"],
        "Invoiced 12 units, but only 7 remain invoiceable.", client=client)

    client.messages.create.assert_not_called()
    client.messages.parse.assert_called_once()
    assert result["subject"] == "Re: Invoice ABC-123"
    assert result["body"] == "Dear vendor, ..."
    assert result["usage"] == {"input_tokens": 100, "output_tokens": 50}
