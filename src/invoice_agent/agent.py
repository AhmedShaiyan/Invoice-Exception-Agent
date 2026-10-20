"""The one bounded agent in this project: investigates *why* an invoice
that already failed deterministic validation failed, using read-only tools
over SAP master/transactional data, and proposes an explanation + suggested
action for a human reviewer. It never approves or rejects anything -- that
capability simply doesn't exist as a tool here, not merely disabled by
convention. See docs/design_decisions.md for why this step is agentic and
honest acknowledgement that a fixed workflow would also work at this scope.

Design: a manual tool-use loop (not the SDK's beta tool_runner), because the
hard tool-call cap needs to be enforced by *never executing* a call past the
limit, not by a retry/backoff hook -- simplest to reason about and log with
a loop you write yourself. Investigation and reporting are two separate API
calls: the loop gathers evidence with tools only, then one final call (no
tools, structured output) turns the gathered evidence into the
explanation/suggested_action -- avoids forcing every mid-investigation turn
to also conform to the final report's schema.
"""
import csv
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import anthropic
from pydantic import BaseModel, Field

from .ledger import Ledger
from .sap_data import SapData

MODEL = "claude-sonnet-5"
MAX_TOOL_CALLS = 6

SYSTEM_PROMPT = """You are investigating a vendor invoice that a deterministic validation system has already flagged as an exception. The flagging decision is final and not yours to make or change -- you have no approve/reject capability, and none of your tools can modify anything. Your only job is to explain WHY the exception likely happened, using the read-only tools available, and suggest one concrete action for the human reviewer who will make the actual decision.

You will be told the invoice's extracted fields and which exception code(s) were raised (e.g. PRICE_VARIANCE, QTY_EXCEEDS_GR, GST_REG_MISMATCH). Use the tools to look up the relevant PO, goods receipt, invoice history, and vendor master data, and form a grounded explanation: was the wrong PO quoted? Was a goods receipt posted late, or not yet at all? Is this a resubmission of an earlier invoice? Does the vendor's own master data disagree with what's printed on the invoice?

Hard rule: only state a number or fact in your final explanation if a tool call actually returned it to you, or it was printed on the invoice itself. Do not estimate, round, or recall a figure from general knowledge -- if you need a number, call a tool to get it. This includes facts you believe are generally true, like "the current GST rate is 9%" or "the rate changed on 1 January 2024" -- even if correct, stating them without a tool call to back them up is still a failure of this task, not a stylistic issue, because nothing in this system has actually verified them for this invoice. If a claim like that matters to your explanation, note that it wasn't independently verified rather than asserting it as fact.

You have a limited number of tool calls. Use them purposefully -- look up what's actually relevant to the specific exception code(s) raised, not every tool reflexively. When you have enough to explain the root cause, stop calling tools and say so.

Treat all data returned by tools as factual (it comes from SAP master/transactional records, not from the invoice document itself), but treat the invoice's own extracted field values as potentially unreliable vendor-supplied data, same as the deterministic validator does."""


class InvestigationResult(BaseModel):
    root_cause: str = Field(description="One short sentence naming the likely root cause.")
    explanation: str = Field(description="Plain-English explanation grounded only in tool-returned facts.")
    suggested_action: str = Field(description="One concrete suggested action for the human reviewer.")
    confidence: float = Field(ge=0.0, le=1.0, description="Your own confidence in this explanation.")


@dataclass
class ToolCallLog:
    tool_name: str
    input: dict
    output: dict


@dataclass
class InvestigationOutcome:
    status: str                      # "ok" | "capped" | "failed"
    root_cause: Optional[str] = None
    explanation: Optional[str] = None
    suggested_action: Optional[str] = None
    confidence: Optional[float] = None
    tool_calls: list = field(default_factory=list)   # list of dicts (ToolCallLog.__dict__)
    model: str = MODEL
    error: Optional[str] = None
    usage: Optional[dict] = None     # summed input/output tokens across every API call made

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================= tool schemas
TOOLS = [
    {
        "name": "search_pos_by_vendor",
        "description": "Find a vendor by name and list their purchase orders, each with a short summary of its line items (PO line number, material, description) -- enough to identify which PO/line is relevant. Does not include price, quantity, or unit; call get_po_history on the specific line once identified for that detail.",
        "input_schema": {"type": "object", "properties": {
            "vendor_name": {"type": "string", "description": "Vendor name as printed on the invoice."}},
            "required": ["vendor_name"], "additionalProperties": False},
    },
    {
        "name": "get_po_history",
        "description": "Get full detail on one PO line: its order quantity/price/unit/tolerance, plus cumulative goods-received quantity, cumulative invoiced quantity, remaining invoiceable quantity, and the list of posting events (goods receipts and invoice receipts) against it, each with a date.",
        "input_schema": {"type": "object", "properties": {
            "ebeln": {"type": "string", "description": "PO number."},
            "ebelp": {"type": "string", "description": "PO line item number, e.g. '00010'."}},
            "required": ["ebeln", "ebelp"], "additionalProperties": False},
    },
    {
        "name": "get_goods_receipts",
        "description": "Get the raw goods-movement documents (MSEG) for one PO line -- each actual receiving/return posting with its document number, movement type, quantity, and posting date. Finer-grained than get_po_history's event list.",
        "input_schema": {"type": "object", "properties": {
            "ebeln": {"type": "string", "description": "PO number."},
            "ebelp": {"type": "string", "description": "PO line item number, e.g. '00010'."}},
            "required": ["ebeln", "ebelp"], "additionalProperties": False},
    },
    {
        "name": "find_prior_invoices_from_vendor",
        "description": "List invoices already on file (posted) from a given vendor, most recent first -- use this to check for resubmission or duplicate patterns.",
        "input_schema": {"type": "object", "properties": {
            "vendor_name": {"type": "string", "description": "Vendor name as printed on the invoice."},
            "limit": {"type": "integer", "description": "Max results, default 10."}},
            "required": ["vendor_name"], "additionalProperties": False},
    },
    {
        "name": "get_vendor_master",
        "description": "Look up a vendor's master record: registered name, address, GST registration number, payment terms, and whether they're centrally blocked.",
        "input_schema": {"type": "object", "properties": {
            "vendor_name": {"type": "string", "description": "Vendor name as printed on the invoice."}},
            "required": ["vendor_name"], "additionalProperties": False},
    },
]


class Investigator:
    def __init__(self, sap: SapData, ledger: Ledger, sap_dir, client: anthropic.Anthropic = None,
                 max_tool_calls: int = MAX_TOOL_CALLS):
        self.sap = sap
        self.ledger = ledger
        self.client = client or anthropic.Anthropic()
        self.max_tool_calls = max_tool_calls
        self._mseg_by_line = self._load_csv_by_line(sap_dir, "mseg.csv")
        self._ekbe_by_line = self._load_csv_by_line(sap_dir, "ekbe.csv")

    @staticmethod
    def _load_csv_by_line(sap_dir, filename) -> dict:
        out = defaultdict(list)
        with open(Path(sap_dir) / filename) as f:
            for row in csv.DictReader(f):
                out[(row["ebeln"], row["ebelp"])].append(row)
        return out

    # ------------------------------------------------------------- tools
    def _tool_search_pos_by_vendor(self, vendor_name: str) -> dict:
        """Summary only -- ebelp/material/description, just enough to pick
        the right PO/line. Price, quantity, unit, and service-flag are
        deliberately omitted: get_po_history returns full detail on one
        specific line once it's identified, and dumping that detail for
        every line of every PO this vendor has ever placed (one real vendor
        in the dataset has 22 POs) made this the single largest tool
        payload in the loop by a wide margin -- it then gets resent in full
        on every subsequent turn of the conversation, which is the actual
        reason investigation calls cost far more per call than extraction.
        Found live in section 6's eval cost breakdown -- see
        docs/decisions_log.md section 6."""
        lifnr = self.sap.find_vendor_by_name(vendor_name)
        if lifnr is None:
            return {"error": f"No vendor found matching '{vendor_name}'"}
        pos = []
        for ebeln, head in self.sap.po_header.items():
            if head["lifnr"] != lifnr:
                continue
            lines = [{"ebelp": ln["ebelp"], "material": ln["matnr"], "description": ln["txz01"]}
                     for ln in self.sap.po_lines_by_po.get(ebeln, [])]
            pos.append({"ebeln": ebeln, "po_date": head["bedat"], "currency": head["waers"], "lines": lines})
        return {"vendor": self.sap.vendors[lifnr]["name1"], "purchase_orders": pos}

    def _tool_get_po_history(self, ebeln: str, ebelp: str) -> dict:
        ekpo = self.sap.po_lines.get((ebeln, ebelp))
        if ekpo is None:
            return {"error": f"No PO line {ebeln}/{ebelp} found"}
        remaining = self.ledger.remaining_qty(ebeln, ebelp)
        events = [{"type": "goods_receipt" if r["vgabe"] == "1" else "invoice_receipt",
                   "document": r["belnr"], "quantity": r["menge"], "date": r["budat"]}
                  for r in self._ekbe_by_line.get((ebeln, ebelp), [])]
        return {
            "line": {"material": ekpo["matnr"], "description": ekpo["txz01"], "order_qty": ekpo["menge"],
                     "unit": ekpo["meins"], "net_price": ekpo["netpr"], "price_unit": ekpo["peinh"],
                     "tax_code": ekpo["mwskz"], "gr_based_invoice_verification": ekpo["webre"] == "X",
                     "is_service_line": ekpo["is_service"] == "X"},
            "cumulative_received_qty": str(self.ledger.gr_qty[(ebeln, ebelp)]),
            "cumulative_invoiced_qty": str(self.ledger.invoiced_qty[(ebeln, ebelp)]),
            "remaining_invoiceable_qty": str(remaining),
            "events": sorted(events, key=lambda e: e["date"]),
        }

    def _tool_get_goods_receipts(self, ebeln: str, ebelp: str) -> dict:
        rows = self._mseg_by_line.get((ebeln, ebelp), [])
        if not rows:
            return {"movements": [], "note": "No goods movements found for this line (expected for a service line)."}
        return {"movements": [{"document": r["mblnr"], "movement_type": r["bwart"],
                                "quantity": r["menge"], "unit": r["meins"], "date": r["budat"]} for r in rows]}

    def _tool_find_prior_invoices_from_vendor(self, vendor_name: str, limit: int = 10) -> dict:
        lifnr = self.sap.find_vendor_by_name(vendor_name)
        if lifnr is None:
            return {"error": f"No vendor found matching '{vendor_name}'"}
        prior = self.ledger.prior_invoices_from_vendor(lifnr)
        prior_sorted = sorted(prior, key=lambda s: s["invoice_date"], reverse=True)[:limit]
        return {"invoices": prior_sorted}

    def _tool_get_vendor_master(self, vendor_name: str) -> dict:
        lifnr = self.sap.find_vendor_by_name(vendor_name)
        if lifnr is None:
            return {"error": f"No vendor found matching '{vendor_name}'"}
        v = self.sap.vendors[lifnr]
        return {"lifnr": lifnr, "name": v["name1"], "gst_reg_no": v["stcd1"], "address": v["address"],
                "payment_terms": v["zterm"], "blocked": v["sperr"] == "X"}

    def _dispatch(self, name: str, tool_input: dict) -> dict:
        handlers = {
            "search_pos_by_vendor": self._tool_search_pos_by_vendor,
            "get_po_history": self._tool_get_po_history,
            "get_goods_receipts": self._tool_get_goods_receipts,
            "find_prior_invoices_from_vendor": self._tool_find_prior_invoices_from_vendor,
            "get_vendor_master": self._tool_get_vendor_master,
        }
        fn = handlers.get(name)
        if fn is None:
            return {"error": f"Unknown tool '{name}'"}
        return fn(**tool_input)

    # ------------------------------------------------------------- loop
    def investigate(self, invoice_fields: dict, exception_codes: list) -> InvestigationOutcome:
        tool_calls: list = []
        user_brief = (
            f"Invoice fields:\n{json.dumps(invoice_fields, indent=2)}\n\n"
            f"Exception code(s) raised by the deterministic validator: {exception_codes}\n\n"
            "Investigate why, using the tools available, then explain."
        )
        messages = [{"role": "user", "content": user_brief}]
        capped = False
        usage_totals = {"input_tokens": 0, "output_tokens": 0}

        def _accumulate(resp):
            u = getattr(resp, "usage", None)
            if u is not None:
                usage_totals["input_tokens"] += u.input_tokens
                usage_totals["output_tokens"] += u.output_tokens

        # Prompt caching: system+tools are byte-identical on every call this
        # method makes (every turn of this investigation, and every other
        # invoice's investigation in the same run) -- render order is
        # tools -> system -> messages, so a breakpoint on the last system
        # block caches both together. Within one investigation this means
        # turns 2+ don't re-bill the ~1-2K token tool schema set; across
        # investigations in the same eval run it means only the first one
        # pays the cache-write cost (5-minute TTL) for the rest. See
        # docs/decisions_log.md section 6 -- this was identified as the
        # likely-largest fixable cost driver after the eval burned real
        # money without it.
        cached_system = [{"type": "text", "text": SYSTEM_PROMPT,
                           "cache_control": {"type": "ephemeral"}}]

        try:
            while True:
                response = self.client.messages.create(
                    model=MODEL, max_tokens=2048, system=cached_system,
                    tools=TOOLS, messages=messages,
                )
                _accumulate(response)
                if response.stop_reason != "tool_use":
                    break

                tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
                messages.append({"role": "assistant", "content": response.content})

                if len(tool_calls) + len(tool_use_blocks) > self.max_tool_calls:
                    capped = True
                    results = [{"type": "tool_result", "tool_use_id": b.id,
                                "content": json.dumps({"error": "Tool call budget exhausted. Provide your "
                                                                  "best explanation now from what you already have."}),
                                "is_error": True} for b in tool_use_blocks]
                    messages.append({"role": "user", "content": results})
                    break

                results = []
                for b in tool_use_blocks:
                    output = self._dispatch(b.name, b.input)
                    tool_calls.append(ToolCallLog(tool_name=b.name, input=b.input, output=output))
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(output)})
                messages.append({"role": "user", "content": results})

            # Final report: separate structured-output call, no tools, so the
            # schema only has to fit the actual final answer, not every
            # mid-investigation turn.
            report_messages = messages + [{"role": "user", "content":
                "Based on everything above, give your final grounded explanation and suggested action."}]
            # thinking disabled: this call only writes up findings already
            # gathered by the tool-use loop above, not new reasoning -- and
            # Sonnet 5's adaptive thinking is on by default, which (hit this
            # live) can consume an entire modest max_tokens budget on
            # thinking alone, leaving nothing for the actual structured
            # output (stop_reason: max_tokens, parsed_output: None).
            report = self.client.messages.parse(
                model=MODEL, max_tokens=2048, system=cached_system,
                thinking={"type": "disabled"},
                messages=report_messages, output_format=InvestigationResult,
            )
            _accumulate(report)
            result = report.parsed_output
            return InvestigationOutcome(
                status="capped" if capped else "ok",
                root_cause=result.root_cause, explanation=result.explanation,
                suggested_action=result.suggested_action, confidence=result.confidence,
                tool_calls=[asdict(t) for t in tool_calls], model=MODEL, usage=usage_totals,
            )
        except Exception as e:   # noqa: BLE001 -- never let an investigation crash the pipeline
            return InvestigationOutcome(status="failed", error=f"{type(e).__name__}: {e}",
                                         tool_calls=[asdict(t) for t in tool_calls], model=MODEL,
                                         usage=usage_totals)


class VendorQueryDraft(BaseModel):
    subject: str = Field(description="Email subject line.")
    body: str = Field(description="Plain-text email body.")


DRAFT_SYSTEM_PROMPT = (
    "You draft brief, professional vendor correspondence for an accounts payable team. "
    "Only state facts given to you in the invoice fields and findings below -- never invent "
    "amounts, dates, PO numbers, or any fact not explicitly stated. This is a draft a human "
    "reviewer will read, edit, and send themselves; you are not sending anything."
)


def draft_vendor_query(invoice_fields: dict, exception_codes: list, explanation: str,
                        client: anthropic.Anthropic = None) -> dict:
    """Drafts a vendor-facing email requesting a corrected invoice or
    credit note. Reuses an investigation's already-grounded explanation
    rather than calling tools or re-verifying anything itself -- one
    cheap, tool-less call, not a second investigation. A human edits and
    marks it sent from the Invoice Review page; this function never sends
    anything. Returns {"subject", "body", "usage"}."""
    client = client or anthropic.Anthropic()
    prompt = (
        f"Invoice fields:\n{json.dumps(invoice_fields, indent=2)}\n\n"
        f"Exception code(s): {exception_codes}\n\n"
        f"Investigation findings (already grounded in verified data):\n{explanation}\n\n"
        "Draft a brief, professional email to the vendor describing the discrepancy and "
        "requesting a corrected invoice or credit note."
    )
    response = client.messages.parse(
        model=MODEL, max_tokens=1024, system=DRAFT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
        thinking={"type": "disabled"}, output_format=VendorQueryDraft,
    )
    result = response.parsed_output
    usage = getattr(response, "usage", None)
    usage_dict = ({"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}
                  if usage is not None else {})
    return {"subject": result.subject, "body": result.body, "usage": usage_dict}


def groundedness_check(explanation: str, tool_calls: list, invoice_fields: dict = None) -> dict:
    r"""Heuristic check for section 6's eval: extract every number-looking
    token in the explanation and confirm it appears somewhere in either the
    tool outputs or the invoice's own fields (the agent is explicitly
    allowed to restate what's printed on the invoice -- the system prompt's
    "only state what a tool returned" rule is about PO/GR/vendor facts the
    agent had to look up, not the invoice data it was handed as the task).
    Not a proof of groundedness (a model could cite a real number from the
    wrong context, or a legitimate arithmetic derivative like "94 - 56 = 38"
    won't match this check even though it's a valid computation over
    grounded facts) -- but it catches the failure mode that matters most: a
    figure that appears nowhere in anything the agent was given or looked up.

    Numbers are extracted with a trailing "." stripped (`re.findall` on
    r"\d[\d,]*\.?\d*" otherwise swallows an ordinary sentence-ending period
    into the match, e.g. "...quantity of 4." -> "4." -- which then never
    equals the grounded "4" even though the digit itself is fine. Caught
    live in section 6's eval run: see docs/decisions_log.md section 6.

    A number that fails the strict verbatim check falls through to a
    second pass: is it *derivable* from the grounded evidence? Three
    narrow, explicit rules, checked in this order --
      1. a tax/GST rate restated as a bare percentage (0.09 in evidence,
         "9" or "9%" in the explanation);
      2. the sum or difference of two grounded numbers (e.g. "ordered 10,
         received 7, so 3 short" -- 3 = 10 - 7);
      3. the rounded product of two grounded numbers (e.g. a line total
         computed as quantity x unit price).
    This only makes the check *more* honest, not laxer: the strict pass
    still runs first and unchanged, so a real fabrication that happens
    not to equal any sum/difference/product of the evidence is still
    caught. It does NOT prove the agent actually performed that specific
    computation -- only that the number is consistent with one -- so this
    remains a heuristic, not a proof, same as the strict pass always was."""
    import re
    from decimal import Decimal, InvalidOperation

    def extract(text):
        return {n.rstrip(".") for n in re.findall(r"\d[\d,]*\.?\d*", text)}

    def as_decimal(s):
        try:
            return Decimal(s.replace(",", ""))
        except InvalidOperation:
            return None

    numbers_in_explanation = extract(explanation)
    numbers_in_evidence = set()
    for call in tool_calls:
        numbers_in_evidence.update(extract(json.dumps(call.get("output", call))))
    if invoice_fields is not None:
        numbers_in_evidence.update(extract(json.dumps(invoice_fields)))

    evidence_norm = {e.replace(",", "") for e in numbers_in_evidence}
    not_verbatim = {n for n in numbers_in_explanation if n.replace(",", "") not in evidence_norm}

    evidence_decimals = [d for d in (as_decimal(e) for e in evidence_norm) if d is not None]
    derivable = set()
    for n in not_verbatim:
        d = as_decimal(n)
        if d is None:
            continue
        if any(ev * 100 == d for ev in evidence_decimals):   # rule 1: rate -> percentage
            derivable.add(n)
            continue
        found = False
        for i, a in enumerate(evidence_decimals):
            if found:
                break
            for b in evidence_decimals[i + 1:]:
                if d in (a + b, abs(a - b)):                  # rule 2: sum/difference
                    found = True
                    break
                if d == (a * b).quantize(Decimal("0.01")):     # rule 3: product (e.g. qty x price)
                    found = True
                    break
        if found:
            derivable.add(n)

    ungrounded = not_verbatim - derivable
    return {"grounded": len(ungrounded) == 0, "ungrounded_numbers": sorted(ungrounded),
            "derived_numbers": sorted(derivable)}
