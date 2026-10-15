"""
Generate a small, deterministic mock of SAP purchasing data.

Table names follow SAP S/4HANA conventions so the demo speaks the
language of a finance IT team:
  LFA1  - vendor master
  EKKO  - purchase order header
  EKPO  - purchase order line items
  MSEG  - goods receipts (material document segments)

Output: CSV files in data/sap/
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(42)
OUT = Path(__file__).resolve().parents[1] / "data" / "sap"
OUT.mkdir(parents=True, exist_ok=True)

# stcd1 = GST registration number. All vendors are fictional.
VENDORS = [
    ("100001", "Lion City Office Supplies Pte Ltd",   "201812345K"),
    ("100002", "Merlion Data Systems Pte Ltd",        "201923456M"),
    ("100003", "Straits Facility Services Pte Ltd",   "M90371234X"),
    ("100004", "Orchid Networks Pte Ltd",             "202034567N"),
    ("100005", "Changi Logistics Pte Ltd",            "200945678R"),
    ("100006", "Bukit Print & Media Pte Ltd",         "201756789W"),
    ("100007", "Kallang Cleanroom Solutions Pte Ltd", "M20112345L"),
    ("100008", "Tanjong Cloud Services Pte Ltd",      "202167890D"),
]

# vendor -> (material no, description, unit of measure, unit price SGD)
CATALOGUE = {
    "100001": [("MAT-1001", "A4 Copier Paper (500 sheets)", "BOX", 6.80),
               ("MAT-1002", "Ballpoint Pens, Blue (pack of 50)", "PAK", 18.50),
               ("MAT-1003", "Ergonomic Office Chair", "EA", 289.00)],
    "100002": [("MAT-2001", "Rack Server 2U, 64GB RAM", "EA", 7450.00),
               ("MAT-2002", "NVMe SSD 3.84TB", "EA", 820.00),
               ("MAT-2003", "Server Installation Service", "HR", 145.00)],
    "100003": [("MAT-3001", "Office Cleaning - Monthly", "MON", 3200.00),
               ("MAT-3002", "Pest Control Treatment", "EA", 480.00)],
    "100004": [("MAT-4001", "48-Port Network Switch", "EA", 2150.00),
               ("MAT-4002", "Cat6 Patch Cable 3m", "EA", 4.20),
               ("MAT-4003", "Wireless Access Point", "EA", 395.00)],
    "100005": [("MAT-5001", "Inbound Freight - Pallet", "PAL", 85.00),
               ("MAT-5002", "Customs Clearance Fee", "EA", 160.00)],
    "100006": [("MAT-6001", "Product Datasheet Printing (1000 pcs)", "LOT", 640.00),
               ("MAT-6002", "Event Banner 2m x 1m", "EA", 120.00)],
    "100007": [("MAT-7001", "Cleanroom Gloves (box of 100)", "BOX", 32.00),
               ("MAT-7002", "ESD-Safe Wipes (case)", "CS", 210.00),
               ("MAT-7003", "Cleanroom Certification Audit", "EA", 2800.00)],
    "100008": [("MAT-8001", "GPU Cloud Compute (per node-hour)", "HR", 12.40),
               ("MAT-8002", "Managed Backup Storage (per TB-month)", "TB", 38.00)],
}

QTY_RANGE = {"EA": (1, 12), "BOX": (10, 200), "PAK": (5, 40), "HR": (20, 400),
             "MON": (1, 3), "PAL": (5, 30), "LOT": (1, 4), "CS": (2, 20), "TB": (5, 60)}


def build():
    start = date(2026, 8, 3)
    ekko, ekpo, mseg = [], [], []
    po_base, doc_no = 4500012001, 5000034001

    for i in range(24):
        lifnr = VENDORS[i % len(VENDORS)][0]
        ebeln = str(po_base + i)
        bedat = start + timedelta(days=random.randint(0, 40))
        ekko.append({"ebeln": ebeln, "lifnr": lifnr, "bedat": bedat.isoformat(),
                     "waers": "SGD", "ekorg": "SG01", "bukrs": "SG10"})

        catalogue = CATALOGUE[lifnr]
        items = random.sample(catalogue, k=random.randint(1, len(catalogue)))
        for n, (matnr, txz01, meins, netpr) in enumerate(items, start=1):
            ebelp = f"{n * 10:05d}"
            menge = random.randint(*QTY_RANGE[meins])
            ekpo.append({"ebeln": ebeln, "ebelp": ebelp, "matnr": matnr, "txz01": txz01,
                         "menge": menge, "meins": meins, "netpr": f"{netpr:.2f}"})

            # Goods receipt (movement type 101): ~20% of lines only partially received
            received = menge if random.random() > 0.2 else max(1, int(menge * 0.6))
            mseg.append({"mblnr": str(doc_no), "zeile": "0001", "bwart": "101",
                         "ebeln": ebeln, "ebelp": ebelp, "matnr": matnr,
                         "menge": received, "meins": meins,
                         "budat": (bedat + timedelta(days=random.randint(3, 14))).isoformat()})
            doc_no += 1

    lfa1 = [{"lifnr": v[0], "name1": v[1], "stcd1": v[2], "ort01": "Singapore", "land1": "SG"}
            for v in VENDORS]
    return {"lfa1": lfa1, "ekko": ekko, "ekpo": ekpo, "mseg": mseg}


def write(name, rows):
    with open(OUT / f"{name}.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"  {name}.csv  ({len(rows)} rows)")


if __name__ == "__main__":
    print("Writing mock SAP tables to", OUT)
    for table, rows in build().items():
        write(table, rows)
