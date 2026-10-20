"""
Generate a mock of SAP purchasing + invoice-verification data, simplified
but with field names and semantics traceable to real S/4HANA tables -- see
docs/data_sources.md for what each field means and where that definition
comes from, including the places this is a deliberate simplification of
real SAP behaviour (e.g. the ±1% price tolerance here stands in for SAP's
configurable OMR6/"PP" tolerance keys).

Tables, with their real S/4HANA role:
  LFA1  - vendor master
  EKKO  - purchase order header
  EKPO  - purchase order line items (now with PEINH, UEBTO/UNTTO, WEBRE, MWSKZ)
  MSEG  - goods movements (S/4HANA compatibility view over MATDOC; kept as a
          CSV named MSEG here because that's the name anyone with SAP MM
          background will recognise -- see docs/data_sources.md A2)
  EKBE  - PO history: one row per goods-receipt or invoice-receipt posting
          against a PO line (vgabe 1 = GR, 2 = IR), so cumulative quantity
          checks can sum rows instead of looking at a single document
  RBKP  - incoming invoice header (as posted via MIRO)
  RSEG  - incoming invoice line items

RBKP/RSEG/EKBE(vgabe=2) written by this script are *historical* postings
only -- invoices from before the evaluation dataset's time window, used as
backstory for duplicate/near-duplicate detection. The 200 invoices in
data/invoices/ are new arrivals the pipeline has never seen; they are not
written here (scripts/generate_invoices.py does that, and is responsible
for updating the ledger as it "posts" each one, in date order).

Output: CSV files in data/sap/
"""
import csv
import random
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

random.seed(42)
OUT = Path(__file__).resolve().parents[1] / "data" / "sap"
OUT.mkdir(parents=True, exist_ok=True)

# stcd1 = GST registration number. All vendors are fictional. Format per
# docs/data_sources.md B9 (no authoritative check-digit algorithm exists;
# format only, loosely following the documented UEN patterns).
VENDORS = [
    ("100001", "Lion City Office Supplies Pte Ltd",   "201812345K", "21 Tannery Lane, Singapore 347733",      "NT30", False),
    ("100002", "Merlion Data Systems Pte Ltd",        "201923456M", "8 Ubi Road 2, Singapore 408538",          "NT30", False),
    ("100003", "Straits Facility Services Pte Ltd",   "M90371234X", "60 Kaki Bukit Place, Singapore 416214",   "NT60", False),
    ("100004", "Orchid Networks Pte Ltd",             "202034567N", "3 Fusionopolis Way, Singapore 138633",    "NT30", False),
    ("100005", "Changi Logistics Pte Ltd",            "200945678R", "9 Changi South Street 3, Singapore 486361", "NT07", False),
    ("100006", "Bukit Print & Media Pte Ltd",         "201756789W", "2 Kallang Avenue, Singapore 339407",      "NT30", False),
    ("100007", "Kallang Cleanroom Solutions Pte Ltd", "M20112345L", "12 Woodlands Loop, Singapore 738322",     "NT60", False),
    ("100008", "Tanjong Cloud Services Pte Ltd",      "202167890D", "7 Straits View, Singapore 018936",        "NT30", False),
    ("100009", "Jurong Industrial Supplies Pte Ltd",  "201645678F", "18 Boon Lay Way, Singapore 609966",       "NT30", False),
    ("100010", "Marina Catering Group Pte Ltd",       "202278901T", "10 Bayfront Avenue, Singapore 018956",    "NT07", False),
    ("100011", "Sentosa Events & AV Pte Ltd",         "201889012Y", "8 Sentosa Gateway, Singapore 098269",     "NT30", False),
    ("100012", "Pasir Ris Electrical Pte Ltd",        "201990123B", "30 Pasir Ris Drive 3, Singapore 518838",  "NT30", False),
    ("100013", "Woodlands Freight Forwarders Pte Ltd", "200812345Q", "15 Woodlands Industrial Park E1, Singapore 757056", "NT60", False),
    ("100014", "Novena Medical Supplies Pte Ltd",     "201512345S", "10 Sinaran Drive, Singapore 307506",      "NT30", False),
    ("100015", "Bedok Hardware Trading Pte Ltd",      "201234567A", "1 Bedok North Road, Singapore 469661",    "NT30", True),   # blocked vendor
    ("100016", "Yishun Security Systems Pte Ltd",     "202045678C", "51 Yishun Industrial Park A, Singapore 768734", "NT30", True),  # blocked vendor
    ("100017", "Redwood Overseas Equipment Inc",      "202256789E", "1 Raffles Place, Singapore 048616",       "NT30", False),  # USD-denominated vendor
    ("100018", "Punggol Waste Management Pte Ltd",    "201367890G", "80 Punggol Central, Singapore 828761",    "NT60", False),
]

# vendor -> (material no, description, unit of measure, unit price SGD, price_unit, is_service)
CATALOGUE = {
    "100001": [("MAT-1001", "A4 Copier Paper (500 sheets)", "BOX", 6.80, 1, False),
               ("MAT-1002", "Ballpoint Pens, Blue (pack of 50)", "PAK", 18.50, 1, False),
               ("MAT-1003", "Ergonomic Office Chair", "EA", 289.00, 1, False),
               ("MAT-1004", "Whiteboard Markers (box of 12)", "BOX", 9.40, 1, False)],
    "100002": [("MAT-2001", "Rack Server 2U, 64GB RAM", "EA", 7450.00, 1, False),
               ("MAT-2002", "NVMe SSD 3.84TB", "EA", 820.00, 1, False),
               ("MAT-2003", "Server Installation Service", "HR", 145.00, 1, True),
               ("MAT-2004", "Cat6 Patch Cable 0.5m", "EA", 3.80, 100, False)],   # priced per 100 units (PEINH)
    "100003": [("MAT-3001", "Office Cleaning - Monthly", "MON", 3200.00, 1, True),
               ("MAT-3002", "Pest Control Treatment", "EA", 480.00, 1, True),
               ("MAT-3003", "Carpet Shampooing Service", "EA", 650.00, 1, True)],
    "100004": [("MAT-4001", "48-Port Network Switch", "EA", 2150.00, 1, False),
               ("MAT-4002", "Cat6 Patch Cable 3m", "EA", 4.20, 1, False),
               ("MAT-4003", "Wireless Access Point", "EA", 395.00, 1, False),
               ("MAT-4004", "Network Design Consultation", "HR", 180.00, 1, True)],
    "100005": [("MAT-5001", "Inbound Freight - Pallet", "PAL", 85.00, 1, False),
               ("MAT-5002", "Customs Clearance Fee", "EA", 160.00, 1, True)],
    "100006": [("MAT-6001", "Product Datasheet Printing (1000 pcs)", "LOT", 640.00, 1, False),
               ("MAT-6002", "Event Banner 2m x 1m", "EA", 120.00, 1, False),
               ("MAT-6003", "Business Card Printing (500 pcs)", "LOT", 45.00, 1, False)],
    "100007": [("MAT-7001", "Cleanroom Gloves (box of 100)", "BOX", 32.00, 1, False),
               ("MAT-7002", "ESD-Safe Wipes (case)", "CS", 210.00, 1, False),
               ("MAT-7003", "Cleanroom Certification Audit", "EA", 2800.00, 1, True)],
    "100008": [("MAT-8001", "GPU Cloud Compute (per node-hour)", "HR", 12.40, 1, True),
               ("MAT-8002", "Managed Backup Storage (per TB-month)", "TB", 38.00, 1, True)],
    "100009": [("MAT-9001", "Industrial Shelving Unit", "EA", 310.00, 1, False),
               ("MAT-9002", "Safety Gloves (pack of 12)", "PAK", 24.50, 1, False),
               ("MAT-9003", "Forklift Rental - Weekly", "WK", 420.00, 1, True)],
    "100010": [("MAT-1101", "Buffet Catering - per pax", "EA", 28.00, 1, True),
               ("MAT-1102", "Bottled Water (case of 24)", "CS", 14.00, 1, False)],
    "100011": [("MAT-1201", "AV Equipment Rental - Daily", "EA", 950.00, 1, True),
               ("MAT-1202", "Event Staging Setup", "EA", 1800.00, 1, True)],
    "100012": [("MAT-1301", "LED Panel Light 60x60", "EA", 42.00, 1, False),
               ("MAT-1302", "Electrical Cable 2.5mm (100m roll)", "EA", 185.00, 1, False)],
    "100013": [("MAT-1401", "Ocean Freight - 20ft Container", "EA", 2400.00, 1, False),
               ("MAT-1402", "Freight Insurance", "EA", 95.00, 1, True)],
    "100014": [("MAT-1501", "Nitrile Examination Gloves (box of 100)", "BOX", 11.50, 1, False),
               ("MAT-1502", "Surgical Masks (box of 50)", "BOX", 16.00, 1, False)],
    "100015": [("MAT-1601", "Stainless Steel Fasteners (box)", "BOX", 22.00, 1, False)],
    "100016": [("MAT-1701", "CCTV Camera, PoE", "EA", 215.00, 1, False)],
    "100017": [("MAT-1801", "Precision Milling Spindle", "EA", 4200.00, 1, False)],   # USD-denominated
    "100018": [("MAT-1901", "Industrial Waste Collection - Monthly", "MON", 1450.00, 1, True)],
}

QTY_RANGE = {"EA": (1, 12), "BOX": (10, 200), "PAK": (5, 40), "HR": (20, 400),
             "MON": (1, 3), "PAL": (5, 30), "LOT": (1, 4), "CS": (2, 20), "TB": (5, 60),
             "WK": (1, 4)}


def build():
    start = date(2026, 6, 1)
    ekko, ekpo, mseg, ekbe, rbkp, rseg = [], [], [], [], [], []
    po_base, mblnr_no = 4500012001, 5100001001

    lifnrs = [v[0] for v in VENDORS]
    # 150 POs produced ~236 lines, not enough for the 150-realistic +
    # ~66-edge-case dataset (each invoice claims 1-3 lines *exclusively*, see
    # scripts/generate_invoices.py) -- pick_po() was silently skipping once
    # the pool ran dry (29 invoices missing with no error). 400 POs gives
    # roughly 630 lines, comfortable headroom.
    n_po = 400
    for i in range(n_po):
        lifnr = lifnrs[i % len(lifnrs)]
        ebeln = str(po_base + i)
        bedat = start + timedelta(days=random.randint(0, 100))
        waers = "USD" if lifnr == "100017" else "SGD"
        ekko.append({"ebeln": ebeln, "lifnr": lifnr, "bedat": bedat.isoformat(),
                     "waers": waers, "ekorg": "SG01", "bukrs": "SG10"})

        catalogue = CATALOGUE[lifnr]
        n_lines = random.randint(1, min(4, len(catalogue)))
        items = random.sample(catalogue, k=n_lines)
        for n, (matnr, txz01, meins, netpr, peinh, is_service) in enumerate(items, start=1):
            ebelp = f"{n * 10:05d}"
            # A PEINH > 1 ("price per 100 units") only makes sense ordered in
            # bulk -- the generic per-UoM qty range (e.g. "EA": 1-12) would
            # produce nonsense like "1 EA at $3.80 per 100".
            menge = random.randint(200, 600) if peinh > 1 else random.randint(*QTY_RANGE[meins])
            webre = not is_service   # GR-based invoice verification: off for service lines
            ekpo.append({"ebeln": ebeln, "ebelp": ebelp, "matnr": matnr, "txz01": txz01,
                         "menge": menge, "meins": meins, "netpr": f"{netpr:.2f}", "peinh": peinh,
                         "uebto": "10.0", "untto": "10.0", "webre": "X" if webre else "",
                         "mwskz": "SR", "is_service": "X" if is_service else ""})

            if is_service:
                continue   # service lines: no goods movement expected at all

            # Goods receipt(s), movement type 101. ~30% of lines get a second,
            # later partial posting (so MSEG/EKBE show >1 GR per line, like a
            # real split delivery), and ~8% also get a 122 return afterwards.
            first_received = menge if random.random() > 0.25 else max(1, int(menge * 0.6))
            gr_postings = [first_received]
            budat1 = bedat + timedelta(days=random.randint(3, 14))
            budats = [budat1]
            if first_received < menge and random.random() > 0.4:
                second = menge - first_received
                gr_postings.append(second)
                budats.append(budat1 + timedelta(days=random.randint(2, 10)))

            for qty, budat in zip(gr_postings, budats):
                mblnr = str(mblnr_no)
                mseg.append({"mblnr": mblnr, "zeile": "0001", "bwart": "101",
                             "ebeln": ebeln, "ebelp": ebelp, "matnr": matnr,
                             "menge": qty, "meins": meins, "budat": budat.isoformat()})
                ekbe.append({"ebeln": ebeln, "ebelp": ebelp, "vgabe": "1", "belnr": mblnr,
                            "menge": qty, "budat": budat.isoformat()})
                mblnr_no += 1

            if sum(gr_postings) > 3 and random.random() < 0.08:
                ret_qty = max(1, int(sum(gr_postings) * 0.15))
                ret_date = budats[-1] + timedelta(days=random.randint(1, 5))
                mblnr = str(mblnr_no)
                mseg.append({"mblnr": mblnr, "zeile": "0001", "bwart": "122",
                             "ebeln": ebeln, "ebelp": ebelp, "matnr": matnr,
                             "menge": ret_qty, "meins": meins, "budat": ret_date.isoformat()})
                ekbe.append({"ebeln": ebeln, "ebelp": ebelp, "vgabe": "1", "belnr": mblnr,
                            "menge": -ret_qty, "budat": ret_date.isoformat()})
                mblnr_no += 1

    lfa1 = [{"lifnr": v[0], "name1": v[1], "stcd1": v[2], "address": v[3],
             "zterm": v[4], "sperr": "X" if v[5] else "", "ort01": "Singapore", "land1": "SG"}
            for v in VENDORS]

    # --- Historical invoices (pre-dataset), for duplicate / near-duplicate backstory ---
    # Pick a handful of (vendor, PO, line) combos that received goods and post a
    # "prior" invoice against each, dated before the evaluation window starts.
    blocked_lifnrs = {v[0] for v in VENDORS if v[5]}
    po_vendor = {h["ebeln"]: h["lifnr"] for h in ekko}
    goods_lines = [(e["ebeln"], e["ebelp"]) for e in ekpo
                   if e["is_service"] != "X" and po_vendor[e["ebeln"]] not in blocked_lifnrs]
    random.shuffle(goods_lines)
    n_historical = 24
    belnr_no = 5105551001
    for hist_idx, (ebeln, ebelp) in enumerate(goods_lines[:n_historical]):
        po = next(p for p in ekpo if p["ebeln"] == ebeln and p["ebelp"] == ebelp)
        head = next(h for h in ekko if h["ebeln"] == ebeln)
        lifnr = head["lifnr"]
        vendor_name = next(v[1] for v in VENDORS if v[0] == lifnr)
        received = sum(D_(r["menge"]) for r in ekbe if r["ebeln"] == ebeln and r["ebelp"] == ebelp)
        inv_qty = min(received, D_(po["menge"]))
        if inv_qty <= 0:
            continue
        price = Decimal(po["netpr"]) / Decimal(po["peinh"])
        amount = (inv_qty * price).quantize(Decimal("0.01"))
        gst = (amount * Decimal("0.09")).quantize(Decimal("0.01"))
        inv_date = date.fromisoformat(head["bedat"]) + timedelta(days=random.randint(10, 20))
        belnr = str(belnr_no); belnr_no += 1
        inv_no = f"{vendor_name.split()[0][:3].upper()}-{inv_date:%y%m}-{901 + hist_idx}"
        rbkp.append({"belnr": belnr, "lifnr": lifnr, "xblnr": inv_no,
                    "bldat": inv_date.isoformat(), "waers": head["waers"],
                    "wrbtr": str((amount + gst).quantize(Decimal("0.01")))})
        rseg.append({"belnr": belnr, "buzei": "001", "ebeln": ebeln, "ebelp": ebelp,
                    "menge": str(inv_qty), "wrbtr": str(amount)})
        ekbe.append({"ebeln": ebeln, "ebelp": ebelp, "vgabe": "2", "belnr": belnr,
                    "menge": str(inv_qty), "budat": inv_date.isoformat()})

    return {"lfa1": lfa1, "ekko": ekko, "ekpo": ekpo, "mseg": mseg,
            "ekbe": ekbe, "rbkp": rbkp, "rseg": rseg}


def D_(x):
    return Decimal(str(x))


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
