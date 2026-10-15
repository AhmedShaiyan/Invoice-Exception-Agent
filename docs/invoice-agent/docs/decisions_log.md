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
