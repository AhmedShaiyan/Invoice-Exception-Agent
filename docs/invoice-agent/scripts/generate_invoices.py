"""
Generate sample vendor invoices (PDF) from the mock SAP data, each tied to a
known scenario, plus a ground-truth file for evaluating the agent.

Scenarios cover the exceptions an accounts-payable team deals with daily:
clean invoices, price and quantity variances against the PO / goods receipt,
missing PO references, GST errors, arithmetic errors and duplicates.
Three different layouts plus one "scanned" image-only PDF keep extraction honest.

Run generate_mock_sap.py first.
Output: data/invoices/*.pdf and data/invoices/ground_truth.json
"""
import copy
import csv
import io
import random
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from invoice_agent.money import D, round2, money_str  # noqa: E402
from invoice_agent.utils import atomic_write_json  # noqa: E402

random.seed(7)
SAP = ROOT / "data" / "sap"
OUT = ROOT / "data" / "invoices"
OUT.mkdir(parents=True, exist_ok=True)

GST_RATE = D("0.09")
BUYER = ("Northwind Semiconductor (Singapore) Pte Ltd",
         "1 Changi Business Park Ave, Singapore 486058")


def read(name):
    with open(SAP / f"{name}.csv") as f:
        return list(csv.DictReader(f))


vendors = {r["lifnr"]: r for r in read("lfa1")}
po_header = {r["ebeln"]: r for r in read("ekko")}
po_lines = defaultdict(list)
for r in read("ekpo"):
    po_lines[r["ebeln"]].append(r)
received = {(r["ebeln"], r["ebelp"]): int(r["menge"]) for r in read("mseg")}

# --------------------------------------------------------------- scenarios
# (scenario, po, layout, expected exception codes)
SCENARIOS = [
    ("clean",                 "4500012001", "modern",  []),
    ("clean",                 "4500012003", "classic", []),
    ("clean",                 "4500012005", "minimal", []),
    ("clean",                 "4500012007", "modern",  []),
    ("clean_partial_receipt", "4500012004", "classic", []),
    ("price_within_tolerance", "4500012009", "minimal", []),
    ("price_mismatch",        "4500012010", "modern",  ["PRICE_VARIANCE"]),
    ("price_mismatch",        "4500012014", "classic", ["PRICE_VARIANCE"]),
    ("qty_exceeds_receipt",   "4500012006", "minimal", ["QTY_EXCEEDS_GR"]),
    ("qty_exceeds_receipt",   "4500012013", "modern",  ["QTY_EXCEEDS_GR"]),
    ("no_po",                 "4500012020", "classic", ["PO_NOT_FOUND"]),
    ("wrong_gst_rate",        "4500012012", "minimal", ["GST_RATE_INVALID"]),
    ("gst_reg_mismatch",      "4500012016", "modern",  ["GST_REG_MISMATCH"]),
    ("arithmetic_error",      "4500012017", "classic", ["TOTAL_MISMATCH"]),
    ("duplicate",             "4500012001", "modern",  ["DUPLICATE_INVOICE"]),
    ("clean_scanned",         "4500012019", "scanned", []),
]


def build_invoice(idx, scenario, ebeln):
    head = po_header[ebeln]
    vendor = vendors[head["lifnr"]]
    inv_date = date.fromisoformat(head["bedat"]) + timedelta(days=random.randint(15, 25))
    inv_no = f"{vendor['name1'].split()[0][:3].upper()}-{inv_date:%y%m}-{1000 + idx * 37}"

    lines = []
    for ln in po_lines[ebeln]:
        qty = received[(ebeln, ln["ebelp"])]          # bill what was received
        price = D(ln["netpr"])
        lines.append({"po_item": ln["ebelp"], "material": ln["matnr"],
                      "description": ln["txz01"], "quantity": qty,
                      "unit": ln["meins"], "unit_price": price})

    gst_rate, gst_reg, po_ref = GST_RATE, vendor["stcd1"], ebeln
    total_override = None

    if scenario == "price_within_tolerance":
        lines[0]["unit_price"] = round2(lines[0]["unit_price"] * D("1.004"))
    elif scenario == "price_mismatch":
        factor = D(str(random.choice([1.08, 1.12])))
        lines[0]["unit_price"] = round2(lines[0]["unit_price"] * factor)
    elif scenario == "qty_exceeds_receipt":
        for ln, po_ln in zip(lines, po_lines[ebeln]):
            ln["quantity"] = int(po_ln["menge"])    # bills full PO qty, but only part was received
    elif scenario == "no_po":
        po_ref = "4500099917"
    elif scenario == "wrong_gst_rate":
        gst_rate = D("0.08")                          # pre-2024 rate
    elif scenario == "gst_reg_mismatch":
        gst_reg = "201799999Z"
    elif scenario == "duplicate":
        return None                                 # filled in by caller

    subtotal = round2(sum((D(l["quantity"]) * l["unit_price"] for l in lines), D("0")))
    gst = round2(subtotal * gst_rate)
    total = round2(subtotal + gst)
    if scenario == "arithmetic_error":
        total_override = round2(total + D("450.00"))

    return {
        "invoice_number": inv_no,
        "invoice_date": inv_date.isoformat(),
        "vendor_name": vendor["name1"],
        "vendor_gst_reg_no": gst_reg,
        "po_number": po_ref,
        "currency": "SGD",
        "lines": [{**l, "amount": round2(D(l["quantity"]) * l["unit_price"])} for l in lines],
        "subtotal": subtotal,
        "gst_rate": gst_rate,
        "gst_amount": gst,
        "total": total_override if total_override is not None else total,
    }


def serialize_invoice(inv):
    """Decimal -> JSON-safe strings for ground_truth.json. Keeps the Decimal
    values in `inv` itself untouched, since to_pdf() still needs them for
    rendering/arithmetic after this is called."""
    return {
        **{k: v for k, v in inv.items() if k not in
           ("lines", "subtotal", "gst_rate", "gst_amount", "total")},
        "lines": [{**l, "unit_price": money_str(l["unit_price"]),
                   "amount": money_str(l["amount"])} for l in inv["lines"]],
        "subtotal": money_str(inv["subtotal"]),
        "gst_rate": str(inv["gst_rate"]),
        "gst_amount": money_str(inv["gst_amount"]),
        "total": money_str(inv["total"]),
    }


# --------------------------------------------------------------- rendering
def money(x):
    return f"{x:,.2f}"


def render_modern(c, inv):
    w, h = A4
    c.setFillColor(colors.HexColor("#1F3A5F"))
    c.rect(0, h - 38 * mm, w, 38 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(18 * mm, h - 16 * mm, inv["vendor_name"])
    c.setFont("Helvetica", 9)
    c.drawString(18 * mm, h - 23 * mm, f"GST Reg. No: {inv['vendor_gst_reg_no']}")
    c.setFont("Helvetica-Bold", 20)
    c.drawRightString(w - 18 * mm, h - 18 * mm, "TAX INVOICE")

    c.setFillColor(colors.black)
    y = h - 52 * mm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(18 * mm, y, "BILL TO")
    c.setFont("Helvetica", 9)
    c.drawString(18 * mm, y - 5 * mm, BUYER[0])
    c.drawString(18 * mm, y - 10 * mm, BUYER[1])
    for i, (k, v) in enumerate([("Invoice No.", inv["invoice_number"]),
                                ("Invoice Date", inv["invoice_date"]),
                                ("Purchase Order", inv["po_number"]),
                                ("Currency", inv["currency"])]):
        c.setFont("Helvetica-Bold", 9)
        c.drawString(125 * mm, y - i * 5 * mm, k)
        c.setFont("Helvetica", 9)
        c.drawRightString(w - 18 * mm, y - i * 5 * mm, v)

    y -= 32 * mm
    cols = [18, 32, 110, 128, 145, 170]
    c.setFillColor(colors.HexColor("#E8EEF5"))
    c.rect(16 * mm, y - 2 * mm, w - 32 * mm, 7 * mm, fill=1, stroke=0)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 8.5)
    for x, t in zip(cols, ["Item", "Description", "Qty", "UoM", "Unit Price", "Amount"]):
        c.drawString(x * mm, y, t)
    c.setFont("Helvetica", 8.5)
    for ln in inv["lines"]:
        y -= 7 * mm
        vals = [ln["po_item"], ln["description"][:42], str(ln["quantity"]), ln["unit"],
                money(ln["unit_price"]), money(ln["amount"])]
        for x, t in zip(cols, vals):
            c.drawString(x * mm, y, t)
    totals_block(c, inv, y - 14 * mm, w)
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(18 * mm, 20 * mm, "Payment terms: 30 days net. Please quote invoice number with payment.")


def render_classic(c, inv):
    w, h = A4
    c.setFont("Times-Bold", 18)
    c.drawCentredString(w / 2, h - 20 * mm, inv["vendor_name"].upper())
    c.setFont("Times-Roman", 10)
    c.drawCentredString(w / 2, h - 26 * mm, f"Company & GST Registration No. {inv['vendor_gst_reg_no']}")
    c.line(15 * mm, h - 30 * mm, w - 15 * mm, h - 30 * mm)
    c.setFont("Times-Bold", 14)
    c.drawCentredString(w / 2, h - 40 * mm, "Tax Invoice")

    c.rect(15 * mm, h - 75 * mm, 90 * mm, 28 * mm)
    c.rect(110 * mm, h - 75 * mm, w - 125 * mm, 28 * mm)
    c.setFont("Times-Bold", 10)
    c.drawString(18 * mm, h - 53 * mm, "Customer:")
    c.setFont("Times-Roman", 10)
    c.drawString(18 * mm, h - 59 * mm, BUYER[0])
    c.drawString(18 * mm, h - 65 * mm, BUYER[1][:48])
    c.drawString(113 * mm, h - 53 * mm, f"Inv #: {inv['invoice_number']}")
    c.drawString(113 * mm, h - 59 * mm, f"Date: {date.fromisoformat(inv['invoice_date']):%d %b %Y}")
    c.drawString(113 * mm, h - 65 * mm, f"Your PO Ref: {inv['po_number']}")

    y = h - 88 * mm
    cols = [17, 30, 120, 140, 165]
    c.setFont("Times-Bold", 10)
    for x, t in zip(cols, ["Line", "Particulars", "Qty", "Rate (S$)", "Total (S$)"]):
        c.drawString(x * mm, y, t)
    c.line(15 * mm, y - 2 * mm, w - 15 * mm, y - 2 * mm)
    c.setFont("Times-Roman", 10)
    for ln in inv["lines"]:
        y -= 7 * mm
        vals = [str(int(ln["po_item"]) // 10), ln["description"][:48],
                f"{ln['quantity']} {ln['unit']}", money(ln["unit_price"]), money(ln["amount"])]
        for x, t in zip(cols, vals):
            c.drawString(x * mm, y, t)
    c.line(15 * mm, y - 3 * mm, w - 15 * mm, y - 3 * mm)
    totals_block(c, inv, y - 12 * mm, w, font="Times")


def render_minimal(c, inv):
    w, h = A4
    c.setFont("Courier-Bold", 12)
    c.drawString(15 * mm, h - 18 * mm, inv["vendor_name"])
    c.setFont("Courier", 9)
    c.drawString(15 * mm, h - 23 * mm, f"GST No. {inv['vendor_gst_reg_no']}")
    c.drawString(15 * mm, h - 35 * mm, "TAX INVOICE")
    rows = [f"Invoice   : {inv['invoice_number']}",
            f"Dated     : {date.fromisoformat(inv['invoice_date']):%d/%m/%Y}",
            f"Order Ref : PO {inv['po_number']}",
            f"Sold To   : {BUYER[0]}"]
    for i, r in enumerate(rows):
        c.drawString(15 * mm, h - (42 + i * 5) * mm, r)
    y = h - 72 * mm
    c.drawString(15 * mm, y, "-" * 92)
    for ln in inv["lines"]:
        y -= 6 * mm
        c.drawString(15 * mm, y, f"{ln['description'][:40]:<40} {ln['quantity']:>5} x {money(ln['unit_price']):>10} = {money(ln['amount']):>12}")
    y -= 6 * mm
    c.drawString(15 * mm, y, "-" * 92)
    c.drawString(15 * mm, y - 6 * mm, f"{'Sub-total':<64}{money(inv['subtotal']):>12}")
    c.drawString(15 * mm, y - 11 * mm, f"{'GST @ ' + str((inv['gst_rate'] * 100).normalize()) + '%':<64}{money(inv['gst_amount']):>12}")
    c.drawString(15 * mm, y - 16 * mm, f"{'AMOUNT DUE (SGD)':<64}{money(inv['total']):>12}")


def totals_block(c, inv, y, w, font="Helvetica"):
    rows = [("Subtotal", inv["subtotal"]),
            (f"GST {(inv['gst_rate'] * 100).normalize()}%", inv["gst_amount"]),
            ("Total Payable (SGD)", inv["total"])]
    for i, (k, v) in enumerate(rows):
        c.setFont(f"{font}-Bold" if i == 2 else ("Times-Roman" if font == "Times" else font), 10)
        c.drawString(130 * mm, y - i * 6 * mm, k)
        c.drawRightString(w - 18 * mm, y - i * 6 * mm, money(v))


RENDERERS = {"modern": render_modern, "classic": render_classic,
             "minimal": render_minimal, "scanned": render_classic}


def to_pdf(inv, layout):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Invoice {inv['invoice_number']}")
    RENDERERS[layout](c, inv)
    c.showPage()
    c.save()
    data = buf.getvalue()
    if layout == "scanned":
        data = make_scanned(data)
    return data


def make_scanned(pdf_bytes):
    """Rasterise, tilt slightly and blur, so there is no text layer to copy.

    Uses pypdfium2 (pure pip install, bundles its own PDFium binary) rather
    than pdf2image, which shells out to the poppler `pdftoppm`/`pdfinfo`
    system binaries. Those aren't pip-installable and aren't present on a
    fresh machine or most deploy targets by default -- the previous
    implementation crashed here on a clean checkout. See docs/decisions_log.md.
    """
    pdf = pdfium.PdfDocument(pdf_bytes)
    bitmap = pdf[0].render(scale=120 / 72)   # 120 DPI (PDFium's native unit is 72 DPI = 1x)
    img = bitmap.to_pil().convert("L")
    pdf.close()
    img = img.rotate(-1.2, expand=True, fillcolor=255).filter(ImageFilter.GaussianBlur(0.5))
    arr = np.asarray(img, dtype=np.int16)
    arr = arr - 18 + np.random.default_rng(7).normal(0, 9, arr.shape)   # grey paper + grain
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    out = io.BytesIO()
    img.save(out, format="PDF", resolution=110)
    return out.getvalue()


# --------------------------------------------------------------- main
if __name__ == "__main__":
    truth, first = [], None
    ground_truth_path = OUT / "ground_truth.json"
    for idx, (scenario, ebeln, layout, codes) in enumerate(SCENARIOS, start=1):
        inv = build_invoice(idx, scenario, ebeln)
        if scenario == "duplicate":
            inv = copy.deepcopy(first)              # same vendor + invoice no. resubmitted
        if idx == 1:
            first = inv
        fname = f"INV_{idx:02d}_{inv['invoice_number']}.pdf"
        (OUT / fname).write_bytes(to_pdf(inv, layout))    # the step that can fail (e.g. rasteriser)

        # Only record truth + flush to disk *after* the PDF write succeeds, so a crash
        # partway through leaves ground_truth.json describing exactly the PDFs that exist,
        # never a partial/corrupt file and never an entry for a PDF that wasn't written.
        truth.append({"file": fname, "scenario": scenario, "layout": layout,
                      "expected_decision": "AUTO_APPROVE" if not codes else "EXCEPTION",
                      "expected_exceptions": codes, "fields": serialize_invoice(inv)})
        atomic_write_json(ground_truth_path, truth)
        print(f"  {fname:<36} {layout:<8} {scenario}")

    print(f"\n{len(truth)} invoices + ground_truth.json written to {OUT}")
