# Decisions log

Short entries for every non-obvious choice, written so I can defend it in an
interview without re-deriving the reasoning from the diff. Newest entries at
the bottom of each section's block; sections match the numbered build plan.

## Section 0 — Housekeeping

**`.gitignore` had a bare `.md` line.** It would have silently excluded
*every* markdown file from git — including `docs/data_sources.md`,
`docs/design_decisions.md`, and this file. Found it while checking why a new
doc wasn't showing as untracked. Removed the line; added `.pytest_cache/`
instead (the thing a stray `.md` rule was probably trying, badly, to catch —
most likely someone's scratch notes file, but as written it nuked the whole
file type). Lesson: a gitignore rule with no `*` or `/` matches by filename
anywhere in the tree, not just one file.

**`pdf2image` → `pypdfium2` for rasterising the "scanned" invoice.**
`pdf2image` shells out to the `poppler` system binaries (`pdftoppm`,
`pdfinfo`). Those aren't a Python package — nothing in `requirements.txt`
could install them — and I proved during the review pass that the generator
crashes on a clean machine without them pre-installed (exactly what
happened in this environment). `pypdfium2` statically bundles the PDFium
binary inside its wheel, so `pip install` is sufficient everywhere,
including whatever host ends up running Streamlit Community Cloud. The
rendering call (`page.render(scale=...)` → `.to_pil()`) is close enough to
the old `convert_from_bytes(...)[0]` that the rest of the tilt/blur/noise
pipeline didn't need to change.

**`docker-compose.yml` now maps host port 5433, not 5432.** On this machine
a native Windows Postgres service was already listening on 5432. Docker's
port publish silently lost that race (or rather, `psycopg` on the host hit
the native service, not the container) and `load_db.py` failed with a
*password authentication failed* error that had nothing to do with the
actual bug. This is not a one-machine fluke — a locally-installed Postgres
on the default port is common on a dev laptop — so the fix belongs in the
compose file, not in a personal workaround. `.env.example` and `.env`
updated to match (`localhost:5433`).

**All money math goes through `src/invoice_agent/money.py` (Decimal,
`ROUND_HALF_UP`, 2dp).** Floats can't represent most decimal fractions
exactly; summing floats and comparing to a GST-rounded total is exactly the
kind of thing that produces a false `TOTAL_MISMATCH` on a clean invoice one
time in a thousand, which is a believability killer for a system whose
entire pitch is "never let an LLM or sloppy arithmetic touch the money."
`D()` builds a Decimal via `str()` first (never `Decimal(float)`, which
imports the float's own imprecision) and `round2()`/`money_str()` are the
only places rounding happens. The same module will back the validation
engine in section 2, so the generator's "expected" numbers and the
checker's "actual" numbers are computed by literally the same rounding
rule — they can't drift apart from each other.

**`ground_truth.json` is written atomically after every invoice, not once
at the end.** The old script built the whole `truth` list in memory and
wrote it in a single `Path.write_text()` after the loop — so the exact
crash I reproduced (rasteriser failure on invoice 16/16) left *zero*
ground truth on disk even though 15 PDFs existed. Now `invoice_agent.utils.
atomic_write_json()` (temp file + `os.replace`, same directory so the
rename is on one filesystem) is called after each successful PDF write, so
a crash on invoice N leaves `ground_truth.json` exactly matching invoices
`1..N-1` — always valid JSON, never a half-written file, never ahead of
what's actually on disk.

**README/requirements.txt overclaims removed, not yet the full rewrite.**
Dropped `langgraph` from requirements (nothing uses it, and CLAUDE.md rules
out agent frameworks absent a concrete justification) and softened
"agentic accounts-payable assistant" framing in the README intro to say
plainly what's deterministic vs. LLM vs. the one bounded agent. Section 9
does the full rewrite once the pipeline backing these claims actually
exists.

## Section 1 — SAP/GST data layer rebuild

**Every SAP field/GST rule added this round is sourced in
`docs/data_sources.md`, including where no authoritative source exists**
(notably: the UEN check-digit algorithm — ACRA has never published it, per
convergent secondary sources, so GST reg. numbers are validated by format
only, not checksummed) **and where this project simplifies real SAP
behaviour on purpose** (the ±1% price tolerance stands in for SAP's
configurable `OMR6`/tolerance-key `PP` mechanism; `UEBTO`/`UNTTO` govern
*quantity* tolerance at goods receipt, not price — I'd originally assumed
they covered price variance, and the source research corrected that before
any code was written against the wrong assumption).

**Ground truth is authored by hand per scenario, never computed by a shared
validator.** `invoice_agent.ledger.Ledger` is read-only from the
generator's point of view — it answers "what quantity is still invoiceable
on this line" and "does this invoice number already exist" so a scenario
can be constructed correctly, but the *expected_decision*/
*expected_exceptions* for every one of the 216 invoices is written directly
in the scenario code. If the generator instead asked a validator "was this
valid?" to label its own test data, the eventual section-2 eval would only
prove the validator agrees with itself — a reviewer would be right to
distrust a 100%-match number built that way. See the module docstring in
`scripts/generate_invoices.py`.

**Duplicate/near-duplicate and sequential-partial-overshoot scenarios don't
need live cross-invoice processing *during generation*.** Duplicates
reference a block of 24 *historical* RBKP/RSEG rows (seeded by
`generate_mock_sap.py`, dated before the dataset's window) rather than
another dataset invoice — this sidesteps needing the generator itself to
process 216 invoices in strict dependency order. The sequential-overshoot
pair is the one place two dataset invoices depend on each other; both are
computed from a single static remaining-quantity snapshot (split
deterministically between them) rather than true live-ledger replay,
because every scenario claims its PO lines exclusively (see next entry) —
so no other invoice can retroactively change that snapshot. The real
dependency this creates is on **section 2's test harness**, which must
replay `ground_truth.json` in chronological order for this pair (and
duplicate detection generally) to validate correctly — see
`docs/design_decisions.md`.

**Every scenario claims its PO lines exclusively from a shared pool (`used_lines`),
never reused.** Early version let two unrelated invoices draw the same PO
line independently, which would have made an accidental third
"sequential-overshoot"-like scenario out of pure bad luck, with no scenario
author aware it had happened. Exclusive claiming means every invoice's
"current remaining quantity" is computed once, correctly, with no
cross-scenario interference. Consequence: the SAP line pool has to be large
enough to cover ~216 invoices × up to 3 lines each without running dry — see
next entry.

**`generate_mock_sap.py`'s PO count went from 150 to 400 after a silent data
loss bug.** `pick_po()` returning `None` when the line pool was exhausted
was being swallowed by a bare `continue` in every scenario generator —
29 invoices went missing with no error, caught only by noticing the output
count (187) didn't match the expected total. Fixed two ways: a much larger
line pool (400 POs ≈ 630 lines, comfortable headroom over the ~216×3 worst
case) so exhaustion shouldn't happen in practice, and a warning printed
from `pick_po()` itself whenever it comes up empty, so a future shortfall
is visible in the generator's own output instead of requiring a manual
scenario-count audit to notice.

**`pick_po()`'s "exclude blocked vendors" safety check initially broke the
one scenario that wants a blocked vendor.** It unconditionally filtered out
any vendor with `LFA1.sperr = 'X'` — correct for every *other* scenario
(nothing should accidentally hand out a blocked vendor's PO), but it also
silently zeroed out `gen_blocked_vendor()`, which explicitly asks for a
blocked vendor by number. Fixed by only applying the blanket exclusion when
the caller didn't name a specific vendor.

**`gst_rounding_difference` was a no-op in its first two versions.** The
scenario is supposed to show the pipeline tolerating a 1-2 cent GST
difference from an alternate IRAS-accepted rounding method (per-line vs.
once-on-subtotal — see `docs/data_sources.md` B8), but double-checking
actually confirmed a real divergence 0/3 times, then 1/3: (1) it's
mathematically impossible for a single-line invoice to diverge at all
(rounding one number twice is a no-op) — needed to require >= 2 lines; (2)
nudging *quantity* to search for a divergent combination is bounded by
actual remaining invoiceable quantity (often single digits), barely
widening the search; nudging *unit price* by 1-cent increments (bounded
instead by the ±1% tolerance, which 5 cents stays well inside for every
catalogue item here) found one in all 3 cases. Verified the final dataset
numerically (not just "it ran without error") before accepting it.

**Filenames are sanitised separately from the invoice-number *data*.** The
near-duplicate scenario deliberately puts a trailing space into
`invoice_number` (that's the point — testing a reformatted near-match), but
that must not leak into the PDF's filename: a trailing space before a file
extension is the kind of thing that silently breaks on some
filesystems/zip/git tooling. `safe_no` strips/replaces risky characters for
the filename only; `ground_truth.json`'s `fields.invoice_number` keeps the
authentic messy value.

**`atomic_write_json` needed a retry on Windows.** Hit a transient
`PermissionError` from `os.replace` at invoice #66/216 — a brief lock from
antivirus or a file indexer, not a real bug, but without a short
retry-with-backoff it would crash a 216-invoice generation run on exactly
this kind of harmless timing collision. Five attempts, exponential backoff
starting at 50ms.

**Statement layout's vendor-name sidebar overlapped the header for long
names** ("Redwood Overseas Equipment Inc" at a flat 26-character cap
collided visually with "TAX INVOICE / STATEMENT") — found by actually
rendering and reading the PDF, not just by the code looking plausible.
Fixed with real word-wrapping against the sidebar's pixel width
(`stringWidth`), not a wider character cap, since any flat cap breaks again
for a long enough name.
