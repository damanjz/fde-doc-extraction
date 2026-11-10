"""Five supplier-invoice layouts and two courier delivery-note layouts.

Each layout draws the values exactly as printed in the Inv / Dn it is given (a planted wrong total is
drawn wrong). Layouts differ in table position, label wording, money style, date style and grouping.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import NamedTuple

from data.common import (
    BUYER_ADDR,
    BUYER_GSTIN,
    BUYER_NAME,
    Courier,
    Supplier,
    fmt_amount,
    fmt_date,
    fmt_qty,
    inr_words,
)
from data.render import Doc, H, W, width

MX = 36.0  # side margin
RIGHT = W - MX


@dataclass
class Ln:
    desc: str
    hsn: str
    uom: str
    qty: Decimal
    qty_places: int
    price: Decimal
    amount: Decimal


@dataclass
class Inv:
    supplier: Supplier
    number: str
    date: date | None
    due: date | None
    terms: int | None
    po_no: str | None
    po_date: date | None
    lines: list[Ln]
    subtotal: Decimal
    rate: int
    split: str  # igst | cgst_sgst
    tax: Decimal  # total GST as stated or implied
    cgst: Decimal
    sgst: Decimal
    round_off: Decimal | None
    total: Decimal
    notes: list[str] = field(default_factory=list)
    gstin: str = ""  # as printed (may be a planted bad checksum); empty means the supplier's own


@dataclass
class Dn:
    courier: Courier
    awb: str
    ship_date: date
    delivery_date: date
    origin: str
    destination: str
    consignor: str
    consignee: str
    packages: int
    weight_kg: Decimal
    contents: str
    reference: str
    received_by: str


# --- shared helpers --------------------------------------------------------------------------------


def wrap(s: str, font: str, size: float, maxw: float) -> list[str]:
    words, out, cur = s.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if width(trial, font, size) <= maxw or not cur:
            cur = trial
        else:
            out.append(cur)
            cur = w
    if cur:
        out.append(cur)
    return out or [""]


class Col(NamedTuple):
    key: str
    title: str
    w: float
    align: str  # l r c


def rate_str(rate: float | int | Decimal) -> str:
    r = Decimal(str(rate))
    return str(int(r)) if r == int(r) else f"{r:.1f}"


def tax_lines(inv: Inv, money: Callable[[Decimal], str]) -> list[tuple[str, str]]:
    half = rate_str(Decimal(inv.rate) / 2)
    if inv.split == "igst":
        return [(f"IGST @ {inv.rate}%", money(inv.tax))]
    return [(f"CGST @ {half}%", money(inv.cgst)), (f"SGST @ {half}%", money(inv.sgst))]


def pan_of(gstin: str) -> str:
    return gstin[2:12]


def draw_table(
    doc: Doc,
    x: float,
    y: float,
    cols: list[Col],
    rows: list[dict],
    *,
    font: str,
    bold: str,
    size: float,
    pad: float = 3.5,
    vlines: bool = True,
    hlines: bool = True,
    head_fill: float | None = 0.88,
    head_text_gray: float = 0.0,
    bottom: float = 740.0,
    on_break: Callable[[], float] | None = None,
    wrap_key: str = "desc",
    head_size: float | None = None,
    outer: bool = True,
    bold_rows: set[int] | None = None,
) -> float:
    total_w = sum(c.w for c in cols)
    lead = size * 1.28
    hs = head_size or size
    head_lines = max(t.count("\n") + 1 for t in (c.title for c in cols))
    head_h = head_lines * hs * 1.25 + 2 * pad

    def header(yy: float) -> float:
        if head_fill is not None:
            doc.rect(x, yy, total_w, head_h, fill=head_fill, stroke=False)
        cx = x
        for c in cols:
            for i, part in enumerate(c.title.split("\n")):
                ty = yy + pad + hs * 0.95 + i * hs * 1.25
                if c.align == "r":
                    doc.text(cx + c.w - pad, ty, part, bold, hs, "r", head_text_gray)
                elif c.align == "c":
                    doc.text(cx + c.w / 2, ty, part, bold, hs, "c", head_text_gray)
                else:
                    doc.text(cx + pad, ty, part, bold, hs, "l", head_text_gray)
            cx += c.w
        return yy + head_h

    def close(top: float, end: float) -> None:
        if vlines:
            cx = x
            for c in cols[:-1]:
                cx += c.w
                doc.line(cx, top, cx, end, 0.4)
        if outer:
            doc.rect(x, top, total_w, end - top, lw=0.6)

    top = y
    yy = header(y)
    doc.line(x, yy, x + total_w, yy, 0.6)
    for ri, row in enumerate(rows):
        wcol = next(c for c in cols if c.key == wrap_key)
        fnt = bold if bold_rows and ri in bold_rows else font
        parts = wrap(str(row.get(wrap_key, "")), fnt, size, wcol.w - 2 * pad - 5)
        rh = len(parts) * lead + 2 * pad - 1
        if yy + rh > bottom and on_break is not None:
            close(top, yy)
            top = on_break()
            yy = header(top)
            doc.line(x, yy, x + total_w, yy, 0.6)
        cx = x
        for c in cols:
            fnt = bold if bold_rows and ri in bold_rows else font
            if c.key == wrap_key:
                for i, part in enumerate(parts):
                    doc.text(cx + pad, yy + pad + size * 0.9 + i * lead, part, fnt, size)
            else:
                val = str(row.get(c.key, ""))
                ty = yy + pad + size * 0.9
                if c.align == "r":
                    doc.text(cx + c.w - pad, ty, val, fnt, size, "r")
                elif c.align == "c":
                    doc.text(cx + c.w / 2, ty, val, fnt, size, "c")
                else:
                    doc.text(cx + pad, ty, val, fnt, size)
            cx += c.w
        yy += rh
        if hlines:
            doc.line(x, yy, x + total_w, yy, 0.3, 0.5)
    close(top, yy)
    return yy


def continued_header(doc: Doc, inv: Inv, font: str, bold: str) -> float:
    doc.new_page()
    doc.text(MX, 50, inv.supplier.name, bold, 11)
    doc.text(RIGHT, 50, f"Invoice No. {inv.number} (continued)", font, 9, "r")
    doc.line(MX, 58, RIGHT, 58, 0.8)
    return 72.0


def ensure_space(doc: Doc, y: float, need: float, inv: Inv, font: str, bold: str) -> float:
    if y + need > H - 60:
        return continued_header(doc, inv, font, bold)
    return y


def add_page_numbers(doc: Doc, font: str = "arial", size: float = 7.5) -> None:
    n = len(doc.pages)
    for i, page in enumerate(doc.pages):
        doc.pages, saved = [page], doc.pages
        doc.text(W / 2, H - 22, f"Page {i + 1} of {n}", font, size, "c", 0.35)
        doc.pages = saved


def line_rows(inv: Inv, lakh: bool, price_places: int = 2, qty_uom: bool = False) -> list[dict]:
    rows = []
    for i, ln in enumerate(inv.lines, 1):
        qty = fmt_qty(ln.qty, ln.qty_places)
        rows.append(
            {
                "sl": str(i),
                "desc": ln.desc,
                "hsn": ln.hsn,
                "uom": ln.uom,
                "qty": f"{qty} {ln.uom}" if qty_uom else qty,
                "rate": fmt_amount(ln.price, lakh, price_places),
                "amt": fmt_amount(ln.amount, lakh),
            }
        )
    return rows


def notes_block(
    doc: Doc,
    x: float,
    y: float,
    inv: Inv,
    font: str,
    bold: str,
    size: float = 8,
    label: str = "Notes",
    maxw: float = 250.0,
) -> float:
    if not inv.notes:
        return y
    doc.text(x, y, label + ":", bold, size)
    y += size * 1.35
    for note in inv.notes:
        for part in wrap(note, font, size, maxw):
            doc.text(x, y, part, font, size)
            y += size * 1.3
    return y


def bank_block(doc: Doc, x: float, y: float, s: Supplier, font: str, bold: str, size: float = 8) -> float:
    doc.text(x, y, "Bank details", bold, size)
    for i, t in enumerate([f"Bank: {s.bank[0]}", f"A/c No.: {s.bank[1]}", f"IFSC: {s.bank[2]}"]):
        doc.text(x, y + (i + 1) * size * 1.3, t, font, size)
    return y + 4 * size * 1.3


def date_or_blank(d: date | None, style: str) -> str:
    return fmt_date(d, style) if d else ""


# --- layout 1: classic tax invoice (Arial, lakh grouping, d-mon-y) ---------------------------------


def layout1(doc: Doc, inv: Inv) -> None:
    f, fb = "arial", "arial-b"
    s, ds = inv.supplier, "d-mon-y"
    doc.text(MX, 56, s.name, fb, 15)
    for i, a in enumerate(s.addr):
        doc.text(MX, 71 + i * 11, a, f, 8.5)
    doc.text(MX, 95, f"GSTIN: {inv.gstin or s.gstin}    PAN: {pan_of(s.gstin)}", f, 8.5)
    doc.text(RIGHT, 56, "TAX INVOICE", fb, 17, "r")
    doc.text(RIGHT, 69, "Original for Recipient", f, 7.5, "r", 0.4)
    doc.line(MX, 106, RIGHT, 106, 1.0)

    doc.rect(MX, 114, RIGHT - MX, 88)
    doc.line(292, 114, 292, 202, 0.5)
    rows = [
        ("Invoice No.", inv.number),
        ("Invoice Date", date_or_blank(inv.date, ds)),
        ("Due Date", date_or_blank(inv.due, ds)),
    ]
    if inv.terms and not inv.due:
        rows[2] = ("Payment Terms", f"Net {inv.terms} days")
    if inv.po_no:
        rows.append(("Your PO Ref.", f"{inv.po_no} dt {fmt_date(inv.po_date, ds)}" if inv.po_date else inv.po_no))
    for i, (k, v) in enumerate(rows):
        doc.text(44, 131 + i * 16, k, f, 8.5, gray=0.3)
        doc.text(120, 131 + i * 16, v, fb, 9)
    doc.text(300, 129, "Bill To", f, 8, gray=0.3)
    doc.text(300, 142, BUYER_NAME, fb, 9.5)
    for i, a in enumerate(BUYER_ADDR):
        doc.text(300, 154 + i * 11, a, f, 8.5)
    doc.text(300, 180, f"GSTIN: {BUYER_GSTIN}", f, 8.5)
    doc.text(300, 192, "Place of supply: Telangana (36)", f, 8.5)

    cols = [
        Col("sl", "Sl", 24, "c"),
        Col("desc", "Description of Goods", 205, "l"),
        Col("hsn", "HSN/SAC", 58, "l"),
        Col("qty", "Qty", 58, "r"),
        Col("uom", "UoM", 36, "c"),
        Col("rate", "Rate (₹)", 70, "r"),
        Col("amt", "Amount (₹)", 72, "r"),
    ]
    y = draw_table(
        doc,
        MX,
        214,
        cols,
        line_rows(inv, True),
        font=f,
        bold=fb,
        size=8.5,
        bottom=730,
        on_break=lambda: continued_header(doc, inv, f, fb),
    )
    y = ensure_space(doc, y, 190, inv, f, fb)
    y += 20
    money = lambda v: fmt_amount(v, True)  # noqa: E731
    rows = [("Taxable Value", money(inv.subtotal))] + tax_lines(inv, money)
    rows.append(("Total Tax Amount", money(inv.tax)))
    if inv.round_off is not None:
        rows.append(("Round off", fmt_amount(inv.round_off, True)))
    sx = 330
    for i, (k, v) in enumerate(rows):
        doc.text(sx, y + i * 14, k, f, 9)
        doc.text(RIGHT - 6, y + i * 14, v, f, 9, "r")
    yt = y + len(rows) * 14 + 2
    doc.rect(sx - 6, yt - 11, RIGHT - sx + 6, 18, fill=0.9)
    doc.text(sx, yt + 2, "Total Amount (₹)", fb, 10)
    doc.text(RIGHT - 6, yt + 2, "₹ " + money(inv.total), fb, 10, "r")
    ny = notes_block(doc, MX, y - 4, inv, f, fb, maxw=250)
    for i, part in enumerate(wrap("Amount in words: " + inr_words(inv.total), f, 8.5, 270)):
        doc.text(MX, max(ny, y + 40) + 6 + i * 11, part, f, 8.5)
    yb = max(yt + 36, ny + 50)
    bank_block(doc, MX, yb, s, f, fb)
    doc.text(RIGHT, yb + 12, f"For {s.name}", fb, 8.5, "r")
    doc.text(RIGHT, yb + 52, "Authorised Signatory", f, 8, "r", 0.3)
    doc.text(
        W / 2, H - 36, "This is a computer generated invoice. Subject to Hyderabad jurisdiction.", f, 7.5, "c", 0.4
    )
    add_page_numbers(doc, f)


# --- layout 2: modern, table directly under a header band (Calibri, western grouping, Rs.) ---------


def layout2(doc: Doc, inv: Inv) -> None:
    f, fb = "calibri", "calibri-b"
    s, ds = inv.supplier, "dmy-slash"
    doc.rect(0, 0, W, 72, fill=0.17, stroke=False)
    doc.text(MX, 44, s.name, fb, 17, gray=1.0)
    doc.text(RIGHT, 46, "INVOICE", fb, 22, "r", 1.0)
    for i, a in enumerate(s.addr):
        doc.text(MX, 94 + i * 11.5, a, f, 9)
    doc.text(MX, 94 + 2 * 11.5, f"GSTIN {inv.gstin or s.gstin}", f, 9)
    doc.text(MX, 94 + 3 * 11.5, f"{s.phone}   {s.email}", f, 9)
    meta = [("Invoice #", inv.number)]
    if inv.date:
        meta.append(("Date", fmt_date(inv.date, ds)))
    if inv.due:
        meta.append(("Due", fmt_date(inv.due, ds)))
    elif inv.terms:
        meta.append(("Terms", f"Net {inv.terms}"))
    if inv.po_no:
        meta.append(("PO", inv.po_no))
    for i, (k, v) in enumerate(meta):
        doc.text(385, 94 + i * 13, k, f, 9, gray=0.35)
        doc.text(RIGHT, 94 + i * 13, v, fb, 9.5, "r")

    cols = [
        Col("sl", "#", 22, "c"),
        Col("desc", "Item", 216, "l"),
        Col("hsn", "HSN", 56, "l"),
        Col("qty", "Qty", 62, "r"),
        Col("rate", "Unit Price (Rs.)", 85, "r"),
        Col("amt", "Total (Rs.)", 82, "r"),
    ]
    y = draw_table(
        doc,
        MX,
        162,
        cols,
        line_rows(inv, False, qty_uom=True),
        font=f,
        bold=fb,
        size=9.5,
        vlines=False,
        head_fill=0.93,
        outer=False,
        bottom=700,
        on_break=lambda: continued_header(doc, inv, f, fb),
    )
    y = ensure_space(doc, y, 180, inv, f, fb)
    y += 22
    doc.text(MX, y, "Billed to", fb, 9.5, gray=0.35)
    doc.text(MX, y + 14, BUYER_NAME, fb, 10)
    for i, a in enumerate(BUYER_ADDR):
        doc.text(MX, y + 27 + i * 11.5, a, f, 9)
    doc.text(MX, y + 27 + 2 * 11.5, f"GSTIN {BUYER_GSTIN}", f, 9)
    ny = notes_block(doc, MX, y + 80, inv, f, fb, 9, "Remarks", 240)

    money = lambda v: "Rs. " + fmt_amount(v, False)  # noqa: E731
    rows = [("Subtotal", money(inv.subtotal))] + tax_lines(inv, money)
    if inv.round_off is not None:
        rows.append(("Round off", fmt_amount(inv.round_off, False)))
    px, pw = 350.0, RIGHT - 350.0
    ph = len(rows) * 15 + 34
    doc.rect(px, y - 12, pw, ph, lw=0.6)
    for i, (k, v) in enumerate(rows):
        doc.text(px + 8, y + 2 + i * 15, k, f, 9.5)
        doc.text(RIGHT - 8, y + 2 + i * 15, v, f, 9.5, "r")
    yt = y + 2 + len(rows) * 15
    doc.rect(px, yt - 9, pw, 25, fill=0.17, stroke=False)
    doc.text(px + 8, yt + 8, "Grand Total", fb, 11, gray=1.0)
    doc.text(RIGHT - 8, yt + 8, money(inv.total), fb, 11, "r", 1.0)
    yb = max(ny + 10, y + ph + 14)
    bank_block(doc, MX, yb, s, f, fb, 9)
    doc.text(RIGHT, yb + 12, "Thank you for your business.", f, 9.5, "r", 0.3)
    add_page_numbers(doc, f)


# --- layout 3: bilingual labels, romanised Hindi (Segoe UI, lakh grouping, rupee sign) -------------


def layout3(doc: Doc, inv: Inv) -> None:
    f, fb = "segoe", "segoe-b"
    s, ds = inv.supplier, "dmy-dot"
    doc.text(W / 2, 50, s.name, fb, 16, "c")
    for i, a in enumerate(s.addr):
        doc.text(W / 2, 65 + i * 11.5, a, f, 9, "c")
    doc.text(W / 2, 88, f"GSTIN / GST Sankhya: {inv.gstin or s.gstin}", f, 9, "c")
    doc.text(W / 2, 108, "TAX INVOICE / Kar Chalan", fb, 12, "c")
    doc.line(MX, 116, RIGHT, 116, 1.0)

    doc.rect(MX, 124, RIGHT - MX, 84)
    doc.line(300, 124, 300, 208, 0.5)
    rows = [
        ("Bill Sankhya / Invoice No.", inv.number),
        ("Tarikh / Date", date_or_blank(inv.date, ds)),
        ("Bhugtan Tarikh / Due Date", date_or_blank(inv.due, ds)),
    ]
    if inv.terms and not inv.due:
        rows[2] = ("Bhugtan Shart / Terms", f"{inv.terms} din / days")
    if inv.po_no:
        rows.append(("Kharid Aadesh / PO No.", inv.po_no))
    for i, (k, v) in enumerate(rows):
        doc.text(44, 141 + i * 15.5, k, f, 8, gray=0.3)
        doc.text(190, 141 + i * 15.5, v, fb, 8.5)
        if not v:
            doc.line(190, 142 + i * 15.5, 280, 142 + i * 15.5, 0.4)
    doc.text(308, 139, "Grahak / Bill To", f, 8, gray=0.3)
    doc.text(308, 152, BUYER_NAME, fb, 9.5)
    for i, a in enumerate(BUYER_ADDR):
        doc.text(308, 164 + i * 11, a, f, 8.5)
    doc.text(308, 192, f"GSTIN: {BUYER_GSTIN}", f, 8.5)

    cols = [
        Col("sl", "Kramank\nSr", 34, "c"),
        Col("desc", "Vivaran\nDescription", 211, "l"),
        Col("hsn", "HSN\nHSN", 58, "l"),
        Col("qty", "Matra\nQty", 66, "r"),
        Col("rate", "Dar\nRate", 74, "r"),
        Col("amt", "Rashi\nAmount", 80, "r"),
    ]
    rows_ = line_rows(inv, True, qty_uom=True)
    y = draw_table(
        doc,
        MX,
        222,
        cols,
        rows_,
        font=f,
        bold=fb,
        size=8.5,
        head_size=8,
        bottom=720,
        on_break=lambda: continued_header(doc, inv, f, fb),
    )
    y = ensure_space(doc, y, 190, inv, f, fb)
    y += 16
    money = lambda v: "₹ " + fmt_amount(v, True)  # noqa: E731
    tl = tax_lines(inv, money)
    rows = [("Yog / Subtotal", money(inv.subtotal))] + tl
    if inv.round_off is not None:
        rows.append(("Gol-sankhya / Round off", fmt_amount(inv.round_off, True)))
    sx = 320
    for i, (k, v) in enumerate(rows):
        doc.text(sx, y + i * 14, k, f, 9)
        doc.text(RIGHT - 6, y + i * 14, v, f, 9, "r")
    yt = y + len(rows) * 14 + 2
    doc.rect(sx - 6, yt - 11, RIGHT - sx + 6, 19, fill=0.9)
    doc.text(sx, yt + 3, "Kul Rashi / Grand Total", fb, 9.5)
    doc.text(RIGHT - 6, yt + 3, money(inv.total), fb, 10, "r")
    ny = notes_block(doc, MX, y - 2, inv, f, fb, 8, "Tippani / Notes", 255)
    yb = max(yt + 30, ny + 14)
    bank_block(doc, MX, yb, s, f, fb)
    doc.text(RIGHT, yb + 12, f"For {s.name}", fb, 8.5, "r")
    doc.text(W / 2, H - 38, "Aabhar / Thank you", f, 9, "c", 0.3)
    add_page_numbers(doc, f)


# --- layout 4: summary strip above the table (Times, lakh grouping, Bill No./Dt.) -------------------


def layout4(doc: Doc, inv: Inv) -> None:
    f, fb = "times", "times-b"
    s, ds = inv.supplier, "dmy-dash"
    doc.text(MX, 54, s.name.upper(), fb, 15)
    for i, a in enumerate(s.addr):
        doc.text(MX, 69 + i * 11.5, a, f, 9.5)
    doc.text(MX, 94, f"GSTIN/UIN : {inv.gstin or s.gstin}", f, 9.5)
    doc.text(MX, 106, f"State Name : {s.state}, Code : {s.state_code}", f, 9.5)
    doc.text(RIGHT, 54, "TAX INVOICE", fb, 14, "r")
    meta = [("Bill No. :", inv.number)]
    meta.append(("Dt. :", date_or_blank(inv.date, ds)))
    if inv.due:
        meta.append(("Pay by :", fmt_date(inv.due, ds)))
    elif inv.terms:
        meta.append(("Mode/Terms of Payment :", f"{inv.terms} Days"))
    if inv.po_no:
        meta.append(("Buyer's Order No. :", inv.po_no))
    for i, (k, v) in enumerate(meta):
        yy = 72 + i * 13
        doc.text(375, yy, k, f, 9.5)
        doc.text(RIGHT, yy, v, fb, 10, "r")
    # summary strip first, table later
    money = lambda v: fmt_amount(v, True)  # noqa: E731
    cells = [("Taxable Amount", money(inv.subtotal))]
    cells += [(k, v) for k, v in tax_lines(inv, money)]
    if inv.round_off is not None:
        cells.append(("Round Off", fmt_amount(inv.round_off, True)))
    cells.append(("Net Payable (Rs.)", money(inv.total)))
    cw = (RIGHT - MX) / len(cells)
    y0 = 128
    doc.rect(MX, y0, RIGHT - MX, 36, fill=0.93)
    for i, (k, v) in enumerate(cells):
        x0 = MX + i * cw
        if i:
            doc.line(x0, y0, x0, y0 + 36, 0.4)
        last = i == len(cells) - 1
        doc.text(x0 + cw / 2, y0 + 13, k, f, 8.5, "c")
        doc.text(x0 + cw / 2, y0 + 29, v, fb, 11 if last else 10, "c")
    doc.text(MX, 188, "Buyer (Bill to)", f, 8.5, gray=0.3)
    doc.text(MX, 201, BUYER_NAME, fb, 10)
    doc.text(MX, 213, ", ".join(BUYER_ADDR), f, 9)
    doc.text(MX, 225, f"GSTIN/UIN : {BUYER_GSTIN}   State Name : Telangana, Code : 36", f, 9)

    cols = [
        Col("sl", "No.", 26, "c"),
        Col("desc", "Particulars", 196, "l"),
        Col("hsn", "HSN/SAC", 58, "l"),
        Col("qty", "Qty.", 62, "r"),
        Col("rate", "Rate", 72, "r"),
        Col("uom", "per", 34, "c"),
        Col("amt", "Amount", 75, "r"),
    ]
    rows = line_rows(inv, True, qty_uom=False)
    rows.append({"sl": "", "desc": "Total", "hsn": "", "qty": "", "rate": "", "uom": "", "amt": money(inv.subtotal)})
    y = draw_table(
        doc,
        MX,
        242,
        cols,
        rows,
        font=f,
        bold=fb,
        size=9.5,
        bottom=735,
        head_fill=None,
        bold_rows={len(rows) - 1},
        on_break=lambda: continued_header(doc, inv, f, fb),
    )
    y = ensure_space(doc, y, 150, inv, f, fb)
    y += 16
    for i, part in enumerate(wrap("Amount Chargeable (in words): " + inr_words(inv.total), fb, 9.5, RIGHT - MX - 16)):
        doc.text(MX, y + i * 12, part, fb, 9.5)
    ny = notes_block(doc, MX, y + 30, inv, f, fb, 9, "Remarks", 280)
    yb = max(ny + 8, y + 56)
    bank_block(doc, MX, yb, s, f, fb, 9)
    doc.text(RIGHT, yb + 12, f"for {s.name}", fb, 9.5, "r")
    doc.text(RIGHT, yb + 52, "Authorised Signatory", f, 9, "r")
    doc.text(W / 2, H - 38, "SUBJECT TO HYDERABAD JURISDICTION. This is a Computer Generated Invoice", f, 8, "c", 0.4)
    add_page_numbers(doc, f)


# --- layout 5: ERP plain text (Courier, no borders, ISO dates, INR) --------------------------------


def layout5(doc: Doc, inv: Inv) -> None:
    f, fb = "cour", "cour-b"
    s, size = inv.supplier, 8.6
    cw = width("0", f, size)
    y = 56.0
    ld = size * 1.4

    def put(col: int, text: str, bold: bool = False, right: bool = False) -> None:
        x = MX + col * cw
        doc.text(x, y, text, fb if bold else f, size, "r" if right else "l")

    put(0, "INVOICE", True)
    y += ld * 1.6
    put(0, s.name, True)
    y += ld
    for a in s.addr:
        put(0, a)
        y += ld
    put(0, f"GST Reg. No.: {inv.gstin or s.gstin}")
    y += ld
    put(0, f"Tel: {s.phone}")
    y += ld * 2
    put(0, f"Invoice #   : {inv.number}")
    y += ld
    if inv.date:
        put(0, f"Issued      : {fmt_date(inv.date, 'iso')}")
        y += ld
    if inv.due:
        put(0, f"Payable by  : {fmt_date(inv.due, 'iso')}")
        y += ld
    elif inv.terms:
        put(0, f"Terms       : net {inv.terms} days")
        y += ld
    if inv.po_no:
        put(0, f"Your ref    : {inv.po_no}")
        y += ld
    put(0, "Currency    : INR")
    y += ld * 1.6
    put(0, "Billed to   :")
    put(14, BUYER_NAME)
    y += ld
    for a in BUYER_ADDR:
        put(14, a)
        y += ld
    put(14, f"GSTIN {BUYER_GSTIN}")
    y += ld * 1.8

    # table: Description(0-41) Qty(42-52) Unit price(54-66) Amount(68-84)
    def rule(at: float, x0: int = 0) -> None:
        doc.line(MX + x0 * cw, at, MX + 84.6 * cw, at, 0.5)

    rule(y - size * 1.0)
    put(0, "Description", True)
    put(52, "Qty", True, True)
    put(66, "Unit price", True, True)
    put(84, "Amount", True, True)
    rule(y + size * 0.5)
    y += ld * 1.3
    for ln in inv.lines:
        parts = wrap(ln.desc, f, size, 41 * cw)
        for i, part in enumerate(parts):
            put(0, part)
            if i == 0:
                put(52, fmt_qty(ln.qty, ln.qty_places), right=True)
                put(66, fmt_amount(ln.price, False), right=True)
                put(84, fmt_amount(ln.amount, False), right=True)
            y += ld
    rule(y - ld + size * 0.55)
    y += ld * 0.5
    put(40, "Amount excl. tax", False)
    put(84, fmt_amount(inv.subtotal, False), right=True)
    y += ld
    gst_label = f"GST {rate_str(inv.rate)}%"
    put(40, gst_label)
    put(84, fmt_amount(inv.tax, False), right=True)
    y += ld
    if inv.split == "cgst_sgst":
        half = rate_str(Decimal(inv.rate) / 2)
        put(40, f"(CGST {half}% + SGST {half}%)")
        y += ld
    if inv.round_off is not None:
        put(40, "Rounding")
        put(84, fmt_amount(inv.round_off, False), right=True)
        y += ld
    rule(y - size * 0.9, 40)
    y += 2
    put(40, "Total due INR", True)
    put(84, fmt_amount(inv.total, False), right=True, bold=True)
    y += ld * 2.5
    if inv.notes:
        put(0, "Notes:", True)
        y += ld
        for note in inv.notes:
            for part in wrap(note, f, size, 84 * cw):
                put(0, part)
                y += ld
        y += ld
    put(0, f"Pay to: {s.bank[0]}, A/C {s.bank[1]}, IFSC {s.bank[2]}")
    y += ld * 2
    put(0, "Generated by LedgerPro ERP. No signature required.")
    add_page_numbers(doc, f)


INVOICE_LAYOUTS: dict[int, Callable[[Doc, Inv], None]] = {1: layout1, 2: layout2, 3: layout3, 4: layout4, 5: layout5}


# --- courier delivery notes ------------------------------------------------------------------------


def dn1(doc: Doc, dn: Dn) -> None:
    """Docket-style table (Arial)."""
    f, fb = "arial", "arial-b"
    c, ds = dn.courier, "dmy-slash"
    doc.rect(0, 0, W, 66, fill=0.2, stroke=False)
    doc.text(MX, 38, c.name, fb, 15, gray=1.0)
    doc.text(MX, 54, f"{', '.join(c.addr)}   GSTIN {c.gstin}", f, 8, gray=1.0)
    doc.text(RIGHT, 38, "DELIVERY NOTE", fb, 15, "r", 1.0)
    doc.text(RIGHT, 54, "Consignment docket", f, 8.5, "r", 1.0)
    rows = [
        ("Docket / AWB No.", dn.awb),
        ("Booking date", fmt_date(dn.ship_date, ds)),
        ("Delivery date", fmt_date(dn.delivery_date, ds)),
        ("Origin", dn.origin),
        ("Destination", dn.destination),
        ("Consignor", dn.consignor),
        ("Consignee", dn.consignee),
        ("No. of packages", str(dn.packages)),
        ("Charged weight (kg)", f"{dn.weight_kg:.2f}"),
        ("Contents", dn.contents),
        ("Customer reference", dn.reference),
        ("Received by", dn.received_by),
    ]
    y, rh = 92.0, 26.0
    doc.rect(MX, y, RIGHT - MX, rh * len(rows))
    doc.line(190, y, 190, y + rh * len(rows), 0.4)
    for i, (k, v) in enumerate(rows):
        yy = y + i * rh
        if i:
            doc.line(MX, yy, RIGHT, yy, 0.3, 0.5)
        doc.text(44, yy + 17, k, f, 9, gray=0.3)
        doc.text(198, yy + 17, v, fb, 10)
    yb = y + rh * len(rows) + 30
    doc.rect(MX, yb, 240, 80)
    doc.text(MX + 8, yb + 14, "Receiver's signature and stamp", f, 8, gray=0.4)
    doc.text(W / 2, H - 38, "Goods carried at owner's risk. Subject to Hyderabad jurisdiction.", f, 7.5, "c", 0.4)


def dn2(doc: Doc, dn: Dn) -> None:
    """POD slip, label: value list (Calibri)."""
    f, fb = "calibri", "calibri-b"
    c, ds = dn.courier, "d mon y"
    doc.text(MX, 56, c.name, fb, 16)
    doc.text(MX, 71, ", ".join(c.addr), f, 9)
    doc.text(MX, 83, f"GST No. {c.gstin}", f, 9)
    doc.line(MX, 94, RIGHT, 94, 1.2)
    doc.text(MX, 124, "PROOF OF DELIVERY", fb, 18)
    rows = [
        ("AWB", dn.awb),
        ("Shipped on", fmt_date(dn.ship_date, ds)),
        ("Delivered on", fmt_date(dn.delivery_date, ds)),
        ("From", dn.origin),
        ("To", dn.destination),
        ("Shipper", dn.consignor),
        ("Receiver", dn.consignee),
        ("Pieces", str(dn.packages)),
        ("Weight", f"{dn.weight_kg:.2f} kg"),
        ("Description", dn.contents),
        ("Shipper ref", dn.reference),
    ]
    y = 160.0
    for k, v in rows:
        doc.text(MX, y, k, f, 10, gray=0.35)
        doc.text(160, y, v, fb, 11)
        y += 24
    y += 10
    doc.line(MX, y, RIGHT, y, 0.5)
    y += 28
    doc.text(MX, y, "Signed by (receiver):", f, 10, gray=0.35)
    doc.text(160, y, dn.received_by, fb, 12)
    doc.line(160, y + 6, 360, y + 6, 0.4)
    doc.text(W / 2, H - 38, "Electronically generated proof of delivery.", f, 8, "c", 0.4)


DN_LAYOUTS: dict[int, Callable[[Doc, Dn], None]] = {1: dn1, 2: dn2}
