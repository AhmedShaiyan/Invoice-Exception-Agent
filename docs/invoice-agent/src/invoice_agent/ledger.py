"""In-memory view of the SAP purchasing ledger: cumulative goods-received and
cumulative invoiced quantity per PO line, plus a lookup index for duplicate /
near-duplicate invoice detection.

Used in two places that must stay consistent with each other for different
reasons:

1. The dataset generator (scripts/generate_invoices.py) uses it to know the
   *current* state of the world while constructing a scenario -- e.g. "what
   quantity is still invoiceable on this line right now" so it can build a
   genuine cumulative overshoot across two sequential invoices, or "does an
   invoice with this number from this vendor already exist" so it can build
   a genuine duplicate. The generator updates the ledger as it posts each
   invoice, processing the dataset in invoice-date order -- exactly like a
   real AP inbox.

2. The validation engine (section 2) uses the same class to answer the same
   questions when actually validating an invoice.

Important: the ground-truth *expected decision* for each dataset invoice is
decided by the scenario's own authoring logic (see SCENARIOS in
generate_invoices.py), not by asking this ledger "was that invoice valid?".
If the generator asked a shared validator to both construct the scenario and
label its own answer, the eval would be circular -- it would only prove the
validator agrees with itself. The ledger only supplies *facts* (remaining
qty, existing invoice numbers); deciding what *should* happen with those
facts is scenario-authoring, done once by a human (me) and again,
independently, by the real validator in section 2.
"""
import csv
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

from .money import D


@dataclass
class Ledger:
    gr_qty: dict = field(default_factory=lambda: defaultdict(lambda: D("0")))
    invoiced_qty: dict = field(default_factory=lambda: defaultdict(lambda: D("0")))
    # (lifnr, normalized invoice_number) -> invoice summary dict, for exact-match duplicate lookup
    by_vendor_invno: dict = field(default_factory=dict)
    # lifnr -> list of invoice summary dicts, for near-duplicate / "prior invoices from vendor" lookup
    by_vendor: dict = field(default_factory=lambda: defaultdict(list))

    @classmethod
    def from_csvs(cls, sap_dir) -> "Ledger":
        """Seed a ledger from the static SAP CSVs: sums sap.ekbe for both
        goods-receipt (vgabe=1, net of 122 returns, which are stored with
        negative menge) and historical invoice-receipt (vgabe=2) rows, and
        indexes the historical sap.rbkp/rseg rows for duplicate lookups."""
        sap_dir = Path(sap_dir)
        ledger = cls()
        with open(sap_dir / "ekbe.csv") as f:
            for row in csv.DictReader(f):
                key = (row["ebeln"], row["ebelp"])
                if row["vgabe"] == "1":
                    ledger.gr_qty[key] += D(row["menge"])
                elif row["vgabe"] == "2":
                    ledger.invoiced_qty[key] += D(row["menge"])

        with open(sap_dir / "rbkp.csv") as f:
            for row in csv.DictReader(f):
                summary = {"invoice_number": row["xblnr"], "invoice_date": row["bldat"],
                           "lifnr": row["lifnr"]}
                ledger.by_vendor_invno[(row["lifnr"], cls._norm(row["xblnr"]))] = summary
                ledger.by_vendor[row["lifnr"]].append(summary)
        return ledger

    @staticmethod
    def _norm(invoice_number: str) -> str:
        return invoice_number.strip().upper().replace(" ", "").replace("/", "-")

    def receive_goods(self, ebeln: str, ebelp: str, qty) -> None:
        """qty may be negative, for movement type 122 (return delivery)."""
        self.gr_qty[(ebeln, ebelp)] += D(qty)

    def remaining_qty(self, ebeln: str, ebelp: str):
        key = (ebeln, ebelp)
        return self.gr_qty[key] - self.invoiced_qty[key]

    def find_exact_duplicate(self, lifnr: str, invoice_number: str):
        return self.by_vendor_invno.get((lifnr, self._norm(invoice_number)))

    def find_near_duplicates(self, lifnr: str, invoice_number: str, score_cutoff: float = 82.0):
        """Fuzzy match against this vendor's existing invoice numbers. Returns
        [(score, summary), ...] for anything above score_cutoff that is NOT an
        exact normalized match (exact matches are find_exact_duplicate's job)."""
        norm = self._norm(invoice_number)
        out = []
        for summary in self.by_vendor.get(lifnr, []):
            other_norm = self._norm(summary["invoice_number"])
            if other_norm == norm:
                continue
            score = fuzz.ratio(norm, other_norm)
            if score >= score_cutoff:
                out.append((score, summary))
        return sorted(out, key=lambda t: -t[0])

    def prior_invoices_from_vendor(self, lifnr: str):
        return list(self.by_vendor.get(lifnr, []))

    def record_invoice(self, lifnr: str, invoice_number: str, invoice_date: str,
                        lines, posted: bool = True) -> None:
        """Record an invoice as posted into the ledger: consumes invoiceable
        quantity on each referenced PO line, and indexes it for duplicate /
        near-duplicate / prior-invoice lookups.

        `posted=False` is for exact-duplicate scenarios: a duplicate invoice
        is caught and never actually posts in a real system, so it must not
        consume quantity or become findable as a second "original" itself.
        """
        if not posted:
            return
        summary = {"invoice_number": invoice_number, "invoice_date": invoice_date, "lifnr": lifnr}
        self.by_vendor_invno[(lifnr, self._norm(invoice_number))] = summary
        self.by_vendor[lifnr].append(summary)
        for ln in lines:
            if ln.get("po_item") and ln.get("po_number"):
                self.invoiced_qty[(ln["po_number"], ln["po_item"])] += D(ln["quantity"])
