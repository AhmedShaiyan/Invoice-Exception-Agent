import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from invoice_agent import present  # noqa: E402


def test_format_detail_sentences_known_code():
    sentences = present.format_detail_sentences(
        "QTY_EXCEEDS_GR", [{"invoiced_qty": "12", "remaining_invoiceable_qty": "7"}])
    assert sentences == ["Invoiced 12 units, but only 7 remain invoiceable."]


def test_format_detail_sentences_empty_detail_returns_empty_list():
    assert present.format_detail_sentences("PRICE_VARIANCE", []) == []
    assert present.format_detail_sentences("PRICE_VARIANCE", None) == []


def test_format_detail_sentences_unknown_code_falls_back():
    sentences = present.format_detail_sentences("SOME_NEW_CODE", [{"foo": "bar"}])
    assert sentences == ["foo: bar"]


def test_format_detail_sentences_multiple_occurrences():
    sentences = present.format_detail_sentences(
        "UOM_MISMATCH", [{"invoiced_unit": "EA", "po_unit": "BOX"},
                          {"invoiced_unit": "KG", "po_unit": "TON"}])
    assert len(sentences) == 2


def test_format_tool_call_dict_output():
    line = present.format_tool_call({
        "tool_name": "get_vendor_master",
        "input": {"lifnr": "100001"},
        "output": {"name": "Lion City Office Supplies Pte Ltd", "gst_reg_no": "201812345K"},
    })
    assert "get_vendor_master" in line
    assert "lifnr=100001" in line
    assert "{" not in line and "}" not in line


def test_format_tool_call_list_output():
    line = present.format_tool_call({
        "tool_name": "search_pos_by_vendor", "input": {"lifnr": "100001"},
        "output": [{"ebeln": "4500012001"}, {"ebeln": "4500012002"}],
    })
    assert "2 result(s)" in line


def test_format_tool_call_empty_list_output():
    line = present.format_tool_call({"tool_name": "x", "input": {}, "output": []})
    assert "no results" in line


def test_format_audit_entry_known_actions():
    assert "AUTO_APPROVE" in present.format_audit_entry("validate", {"decision": "AUTO_APPROVE"})
    assert "Reason: late GR" in present.format_audit_entry("approved", {"reason": "late GR"})
    assert "no reason recorded" in present.format_audit_entry("rejected", {})


def test_format_audit_entry_unknown_action_falls_back():
    line = present.format_audit_entry("some_future_action", {"foo": "bar"})
    assert "{" not in line and "}" not in line
