"""End-to-end pipeline: extract -> validate -> (if exception) investigate ->
persist. Ties together sections 2-5 into the one entry point section 6's
eval and section 7's Streamlit app both call, so there's exactly one place
that defines "what happens to an invoice," not two slightly-different copies.

Idempotency check happens first and short-circuits everything else -- the
whole point is to avoid re-running extraction/validation/investigation (and
re-inserting DB rows) for a file whose exact bytes were already processed.

Extraction and validation both run *before* the single ap.invoices insert,
so that insert can carry the real initial decision (AUTO_APPROVE or
EXCEPTION) straight away -- the schema's CHECK constraint on `decision` only
allows AUTO_APPROVE/EXCEPTION/APPROVED/REJECTED, there's no placeholder
"pending" value to insert first and fix up later.
"""
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from . import db
from .agent import Investigator
from .extract import extract_invoice_file
from .idempotency import IdempotencyStore
from .validate import Validator


@dataclass
class PipelineResult:
    file: str
    decision: str
    exception_codes: list
    extraction_status: str
    invoice_id: Optional[int] = None
    investigated: bool = False
    idempotent_hit: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def process_invoice(file_path, validator: Validator, investigator: Optional[Investigator],
                     idempotency: IdempotencyStore, conn=None, client=None,
                     investigate_exceptions: bool = True) -> PipelineResult:
    path = Path(file_path)
    content = path.read_bytes()
    content_hash = idempotency.hash_bytes(content)
    cached = idempotency.get(content_hash)
    if cached is not None:
        return PipelineResult(**{**cached, "idempotent_hit": True})

    extraction = extract_invoice_file(path, client=client)

    if extraction.status != "ok":
        decision, exception_codes = "EXCEPTION", [extraction.exception_code]
        detail_by_code = {extraction.exception_code: {"error": extraction.error, "confidence": extraction.confidence}}
        validation = None
    else:
        validation = validator.validate(extraction.fields)
        decision, exception_codes = validation.decision, validation.exception_codes
        detail_by_code = validation.details

    invoice_id = None
    explanation = suggested_action = None
    investigated = False

    if conn:
        invoice_id = db.insert_invoice(conn, path.name, extraction.fields, decision=decision)
        db.log_audit(conn, invoice_id, "agent:extractor", "extract",
                      payload={"status": extraction.status, "confidence": extraction.confidence,
                               "attempts": extraction.attempts, "usage": extraction.usage},
                      model=extraction.model)
        if validation is not None:
            db.log_audit(conn, invoice_id, "agent:validator", "validate",
                          payload={"decision": decision, "exception_codes": exception_codes})

    # validation is None whenever extraction itself failed/low-confidence --
    # CLAUDE.md scopes the investigator to invoices that failed *validation*,
    # and there's nothing to investigate when there are no extracted fields
    # to look up in the first place. Without this check, every extraction
    # failure also triggered a full (wasted) investigation call -- found
    # live in section 6's eval run, where it meaningfully inflated API
    # spend for zero benefit. See docs/decisions_log.md section 6.
    if decision == "EXCEPTION" and validation is not None and investigate_exceptions and investigator is not None:
        outcome = investigator.investigate(extraction.fields or {}, exception_codes)
        investigated = True
        explanation, suggested_action = outcome.explanation, outcome.suggested_action
        if conn:
            db.log_audit(conn, invoice_id, "agent:investigator", "investigate",
                          payload=outcome.to_dict(), model=outcome.model)

    if conn and exception_codes:
        db.insert_exceptions(conn, invoice_id, exception_codes, detail_by_code=detail_by_code,
                              explanation=explanation, suggested_action=suggested_action)

    result = PipelineResult(file=path.name, decision=decision, exception_codes=exception_codes,
                             extraction_status=extraction.status, invoice_id=invoice_id,
                             investigated=investigated)
    idempotency.record(content_hash, path.name, result.to_dict())
    return result
