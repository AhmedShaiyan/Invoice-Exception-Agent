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
