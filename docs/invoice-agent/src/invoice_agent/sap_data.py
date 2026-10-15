"""Load the static SAP master-data CSVs (vendor master, PO header/items)
into indexed structures. Separate from invoice_agent.ledger.Ledger, which
holds the *moving* parts (quantities consumed, invoices posted) -- this
module is the fixed master data the ledger's facts get checked against."""
import csv
from dataclasses import dataclass, field
from pathlib import Path


def _norm_name(name: str) -> str:
    return " ".join(name.strip().split()).casefold()


@dataclass
class SapData:
    vendors: dict = field(default_factory=dict)          # lifnr -> lfa1 row
    vendors_by_name: dict = field(default_factory=dict)   # normalized name -> lifnr
    po_header: dict = field(default_factory=dict)         # ebeln -> ekko row
    po_lines: dict = field(default_factory=dict)          # (ebeln, ebelp) -> ekpo row
    po_lines_by_po: dict = field(default_factory=dict)    # ebeln -> [ekpo rows]

    @classmethod
    def from_csvs(cls, sap_dir) -> "SapData":
        sap_dir = Path(sap_dir)
        data = cls()
        with open(sap_dir / "lfa1.csv") as f:
            for row in csv.DictReader(f):
                data.vendors[row["lifnr"]] = row
                data.vendors_by_name[_norm_name(row["name1"])] = row["lifnr"]
        with open(sap_dir / "ekko.csv") as f:
            for row in csv.DictReader(f):
                data.po_header[row["ebeln"]] = row
        data.po_lines_by_po = {}
        with open(sap_dir / "ekpo.csv") as f:
            for row in csv.DictReader(f):
                data.po_lines[(row["ebeln"], row["ebelp"])] = row
                data.po_lines_by_po.setdefault(row["ebeln"], []).append(row)
        return data

    def find_vendor_by_name(self, name: str):
        return self.vendors_by_name.get(_norm_name(name))
