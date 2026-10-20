# Decisions log

The 10 most interview-relevant decisions and bugs from building this,
each a few sentences. The full, unabridged log (every section, every
bug, every dead end) is in [`decisions_log_full.md`](decisions_log_full.md)
for anyone who wants the complete history.

## 1. All money math is deterministic Python/SQL -- an LLM never decides pass/fail

The three-way match, GST checks, and the auto-approve/exception decision
are plain `Decimal` arithmetic against SAP-shaped data, not an LLM call.
Claude is used for exactly two things: extracting unstructured invoices
into structured fields, and explaining *why* an exception happened in
plain English. This isn't a style preference -- an LLM is non-deterministic
and can't be held to a hard "zero false approvals" requirement the way a
fixed comparison against a tolerance band can.

## 2. One bounded, read-only investigation agent -- no multi-agent framework

The only agentic part of this system investigates invoices that already
failed deterministic validation: read-only SAP-lookup tools, a hard
tool-call cap, and explicitly no approve/reject capability -- that tool
simply doesn't exist, it isn't disabled by convention. No LangGraph/CrewAI;
a manual tool-use loop was simpler to reason about and cheaper to run for
a task this bounded. See `design_decisions.md` for the full "why not a
framework" reasoning.

## 3. A real ledger-corruption bug: ambiguous PO-line matching credited the wrong PO

`_match_line`'s fallback (material code, then fuzzy description) initially
trusted the *first* matching candidate when a vendor legitimately ordered
the same material via two different POs on one invoice. That silently
credited one PO's quantity ledger while leaving the other's uncredited --
a real false approval (`INV_154`) and a wrong exception code on a second
invoice (`INV_130`), found by investigating a plausible-but-wrong
`QTY_EXCEEDS_GR` flag rather than trusting it. Fixed by requiring the
match to be unambiguous *across distinct POs* before trusting it.

## 4. Extraction model routing: Haiku is fine for typed text, not for degraded scans

Haiku 4.5 was adopted for typed invoices to cut extraction cost, but a
live sample on the "messy_scan" layout (rotated, noisy scans) showed
19.4% accuracy with dangerous overconfidence -- it didn't know it was
wrong. Fixed by routing on a free, local, pre-API signal (does the PDF
have a text layer via `pypdf`), sending only genuinely text-free scans to
Sonnet 5. Confirmed 80% accuracy and correct low-confidence
self-reporting after the fix.

## 5. `ap.invoices.lifnr` was never actually written, for the whole project's life

The column existed in the schema since section 5, documented as "matched
vendor" -- but `insert_invoice`'s own INSERT statement never included it.
Every one of the 217 real rows had `lifnr = NULL` until this was found
while wiring the Worklist page's due-date-from-payment-terms feature (a
join against `sap.lfa1` that silently matched nothing). Fixed the write
path and backfilled all 217 rows for free, by re-running the same
vendor-name lookup the validator already used against already-stored
data -- 214 resolved, 3 unresolved, exactly the dataset's known
unknown-vendor cases.

## 6. Deployment: Databricks Lakebase, and two gotchas worth knowing in advance

Chose Lakebase (Postgres-compatible, free under Databricks Free Edition,
closer to a real enterprise data stack than Neon) for the hosted
database. Two non-obvious things cost real debugging time: a fresh
password role has `CREATEDB` but no `CREATE` privilege on Lakebase's
*shared* default database, so it needs to own a database it created
itself; and the shared daily API quota's write path used Python's local
`date.today()` while every read path used SQL's `CURRENT_DATE` --
harmless most of the day, but capable of splitting one day's usage
across two rows right at a timezone-dependent midnight boundary. Both
fixed; see the full log for the complete Streamlit Cloud deployment saga
(a secrets-nested-under-the-wrong-key issue that took a three-commit
debug cycle with a hash-fingerprint probe to pin down, since nobody
wants a real password printed to a public page to debug it).

## 7. The eval's cost math was itself wrong by 36%, caught before it shipped

The first version of `evaluate.py`'s cost calculation hardcoded Sonnet 5
pricing for every call, including the ~184 invoices actually extracted on
Haiku -- overstating the real run's cost by 36% ($6.77 reported vs. $4.99
actual). Fixed by threading the actual serving model through to the cost
calculation. The real, corrected cost for the full 217-invoice run: ~$4.96.

## 8. The headline metric is false approvals, not accuracy -- because the costs aren't symmetric

A false approval means a wrong invoice gets paid; a false rejection means
a human spends a few minutes on an invoice that was actually fine. Those
aren't the same severity, so the eval reports them separately rather than
blending into one accuracy number. Result: **0 / 61 false approvals**
(the hard requirement), 10 / 156 false rejections (6.4%), each one
individually explained rather than left as a bare count.

## 9. Explanation groundedness: a heuristic, made more honest rather than left wrong

`groundedness_check` flags any number in the agent's explanation that
doesn't appear in the tool outputs or invoice fields it was given. It
originally only matched numbers verbatim, which wrongly penalized
*correct* arithmetic (a rate restated as "9%", a computed difference) as
if it were fabricated. Extended with three narrow derivation rules
(rate-as-percentage, sum/difference, product of two grounded numbers) --
the strict check still runs first, so a real fabrication is still caught.
Re-scored for free against already-captured data: 63.6% -> 86.4% grounded,
with the 9 remaining failures being genuine (including a real instance of
the agent citing "2014" from general knowledge about GST history,
unprompted and unverified).

## 10. Rebuilt the UI around a real reviewer's feedback, not just an eval harness

A hiring manager's review of the deployed demo found it narrated to a
developer (raw JSON, "see CLAUDE.md", a filename dropdown instead of a
worklist) rather than working like a tool an AP team would recognize.
Rebuilt around a Worklist (due dates from vendor payment terms), an
Invoice Review page (original PDF, line-by-line PO match table, a new
"draft vendor query" agent action), and separate Insights/Model
Performance pages for manager vs. engineering audiences. No new
validation logic -- this was a presentation and workflow rebuild on top
of data that already existed. Found four more real bugs along the way
(a `st.dataframe`/pandas-`Styler` silent-failure, an Arrow type-mismatch,
a vendor-scorecard `GROUP BY` bug that split one vendor into two rows by
text case, and the processing-time report's `0.00s`-for-no-data display
bug) -- full details in the archived log.
