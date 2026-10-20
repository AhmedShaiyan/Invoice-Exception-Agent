# Design decisions

Deeper design rationale that doesn't fit in code comments or the decisions
log's one-liners. Each section below is written for the build phase that
introduced it.

## Line matching for the "minimal" layout (section 1)

Three of this project's PDF layouts (modern, classic, tabular) print a PO
item number and/or material code on every line, so matching an extracted
invoice line to a PO line is a direct key lookup. The `minimal` layout
deliberately does not -- see `render_minimal` in `scripts/generate_invoices.py`
-- because real vendor invoices often don't either; a line-matching strategy
that only works when the vendor kindly prints your own PO line number on
their invoice isn't a realistic one.

**The matching strategy, in priority order:**

1. **Material code**, if the extracted text contains one that matches an
   `EKPO.matnr` on the referenced PO. Exact, unambiguous, used when present.
2. **Description similarity + quantity/price consistency**, when no material
   code is available (`minimal` layout, and any OCR'd layout where extraction
   missed the code). Compute a fuzzy string score (`rapidfuzz.fuzz.ratio`)
   between the invoice line's description and every unclaimed PO line's
   `txz01` on the referenced PO. The top match is accepted only if *both*
   hold:
   - similarity score above a threshold (85, chosen generously above typical
     truncation noise -- the `minimal` renderer truncates descriptions to 40
     characters, which lowers the score of a true match less than it raises
     the score of a false one), **and**
   - the invoiced quantity and unit price are *plausible* against that PO
     line (quantity doesn't wildly exceed what's invoiceable, price is in
     the right order of magnitude) -- a description match alone isn't
     enough, because two PO lines can have very similar descriptions (e.g.
     two different paper products) and a human reviewing the same ambiguous
     invoice would also want to sanity-check the numbers, not just the text.
3. **Route to review, unmatched**, if neither of the above clears its bar.
   This is a deliberate choice: silently guessing which PO line an invoice
   line refers to, when the invoice gives genuinely insufficient
   information, is worse than asking a human -- a wrong silent match could
   approve payment against the wrong line's tolerance window. A new
   exception code for this path (distinct from the specific checks that fire
   *after* a line is successfully matched) is part of section 2.

This is implemented in the validation engine (section 2), not the
generator -- the generator's job is only to produce invoices that are
genuinely ambiguous this way (the `minimal` layout, by construction) and
label the expected outcome by hand (see the circularity note in
`scripts/generate_invoices.py`'s module docstring).

## Why the exception investigator is an agent, not a workflow (section 4)

CLAUDE.md requires the opposite of the usual bias: justify reaching for
agentic tool-calling rather than assume it. Here's that argument made
honestly, both directions, with a recommendation -- the same shape the
original architecture review (before any code existed) asked for.

**The case for a fixed workflow instead.** With 5 tools and ~17 exception
codes, the investigation space is small enough to hand-enumerate:
`if GST_REG_MISMATCH: call get_vendor_master` is a one-line, fully testable
mapping. A fixed workflow is strictly more auditable -- for a SOX-style audit
trail, "the code always looks up X for this exception type" is a cleaner
answer than "the model decided to." It's also cheaper and faster (one or two
deterministic lookups beat a multi-turn tool-calling loop), and every one of
the three real investigations run during development above (GST_REG_MISMATCH,
DUPLICATE_INVOICE, VENDOR_BLOCKED) used exactly the 1-2 tools a human
investigating that exception code by hand would reach for first, in an order
a lookup table could have specified in advance. Nothing about those three
runs *required* judgment a workflow couldn't express.

**The case for an agent.** The one scenario in this project's dataset that
actually needed data-dependent branching is QTY_EXCEEDS_GR on a line with a
genuinely ambiguous goods-receipt history (the `sequential_partial_*`
scenario family): whether the right next lookup is "check goods receipts
again," "check prior invoices for a resubmission pattern," or "stop, this is
explained" depends on what the first lookup returns -- not on the exception
code alone, which is the same QTY_EXCEEDS_GR whether the real cause is a
late GR, a resubmission, or a genuine overbill. A fixed `if code == X: call
Y` mapping can't express "call Z only if Y came back ambiguous" without
turning into its own decision tree that's just the agent's judgment,
pre-compiled by hand and therefore unable to adapt to a case the author
didn't anticipate. The investigation run against that exact scenario (see
`docs/decisions_log_full.md` section 4) is the concrete evidence: the agent
called `get_po_history` then `get_goods_receipts`, read that only one GR had
posted and the remaining balance had never arrived, and explained that --
two tools, chosen in response to what the first one returned, not a fixed
sequence.

**Recommendation, honestly stated**: build it as the bounded agent (as done
here), but the justification is narrow and specific -- the QTY_EXCEEDS_GR
ambiguous-history case -- not "agents are generally better at this." For
most of the exception codes in this project, a fixed workflow would score
identically on the real test runs above, and that's a genuine finding, not
a hedge. What actually earns the agentic design is containment, not
necessity: every tool is read-only, there is no approve/reject tool to even
offer the model, and a hard cap means a bad tool-selection sequence wastes
at most a few API calls, never touches data, and never produces a worse
outcome than "the explanation is less useful than it could have been." The
blast radius of being wrong here is close to zero, which is what makes it
defensible to let the model choose at all.

## Near-duplicate detection (section 2)

Two tiers of normalisation, not a fuzzy-match score:

- **Exact duplicate**: same vendor, and the invoice number matches after
  trimming whitespace and case-folding only (`Ledger._norm_strict`).
- **Near duplicate**: same vendor, and the invoice number matches after
  additionally stripping *all* punctuation/whitespace (`Ledger._norm_loose`)
  -- i.e. the same characters in the same order, just reformatted
  ("LIO-2610-1037" / "LIO/2610/1037" / "LIO26101037" all collapse to the
  same loose form).

No generic string-similarity score (edit distance, fuzzy ratio) is used
beyond that. This project's invoice numbers are `VENDOR-YYMM-NNNN`, which
means two *different*, unrelated invoices from the same vendor in the same
month already share an 8-character prefix -- measured real pairs from this
dataset scored as "similar" as 96 on plain edit distance despite being
genuinely unrelated invoices. No fixed cutoff reliably separates that
structural coincidence from a real near-duplicate for IDs built this way.
Every actual near-duplicate this project needs to catch is a
punctuation/whitespace reformat (the realistic failure mode: OCR drops a
dash, someone re-keys with spaces instead of dashes, a system exports with
slashes instead of dashes) -- which `_norm_loose` catches deterministically,
with no tunable threshold and no false-positive risk from prefix
coincidence. A generic fuzzy fallback was tried and removed after it kept
flagging unrelated invoices; see `docs/decisions_log_full.md` section 2.

## Sequential, stateful validation (section 1 → section 2)

Two scenarios in the dataset -- `sequential_partial_1_of_2`/`2_of_2` and the
duplicate/near-duplicate family -- only have a correct answer if the checker
knows what was *already posted* before the invoice under test. SAP gets
this from `EKBE`'s running totals and `RBKP.xblnr`; this project mirrors
that with `invoice_agent.ledger.Ledger`, seeded from the static CSVs
(goods receipts, plus a block of pre-dataset "historical" invoices) and then
updated as the validation engine processes dataset invoices **in
chronological (invoice-date) order**, exactly like a real AP inbox working
through its queue. See `src/invoice_agent/ledger.py` for the fuller
docstring and why this doesn't make the ground truth circular: the ledger
only ever supplies facts (remaining quantity, existing invoice numbers) to
the *generator* when it authors a scenario's expected answer by hand --
deciding what *should* happen with those facts is scenario-authoring, done
once by me, and again independently by the real validator.

**Consequence for section 2's test harness**: the pytest suite that checks
the validation engine against `ground_truth.json` must process entries in
`invoice_date` order and accumulate ledger state as it goes -- validating
each invoice independently/in isolation would silently break exactly the
two scenario families designed to test this behaviour.
