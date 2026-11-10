"""Validation in code. The LLM never decides whether a document is consistent.

Each rule returns Flags: the field paths involved (we cannot tell which of the numbers in a failed
sum is the wrong one, so every field in the sum is flagged) and a rule name.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import gstin
from app.config import settings
from app.normalise import parse_decimal, squash
from app.schema import GST_SLABS, REQUIRED, line_path

TOLERANCE = Decimal("1.00")  # INR, for sums and products (covers round-off)
AWB_RE = re.compile(r"^[A-Z]{0,4}\d{8,12}$")


@dataclass(frozen=True)
class Flag:
    field: str
    rule: str
    message: str


def _d(v: object) -> Decimal | None:
    return parse_decimal(v) if v is not None else None


def _date(v: object) -> date | None:
    try:
        return date.fromisoformat(v) if isinstance(v, str) else None
    except ValueError:
        return None


def check_parse_errors(bad: Iterable[str]) -> list[Flag]:
    return [Flag(p, "unparseable", "value could not be read as the expected type") for p in sorted(bad)]


def check_required(doc_type: str, rec: dict) -> list[Flag]:
    flags = [Flag(f, "missing", "required field is empty") for f in REQUIRED[doc_type] if rec.get(f) in (None, "")]
    if doc_type == "invoice" and not rec.get("line_items"):
        flags.append(Flag("line_items", "missing", "no line items found"))
    return flags


def check_gstin(rec: dict, field: str) -> list[Flag]:
    v = rec.get(field)
    if v in (None, ""):
        return []  # missing is reported by check_required
    ok, why = gstin.validate(str(v))
    return [] if ok else [Flag(field, "gstin_checksum" if "checksum" in why else "gstin_format", f"GSTIN: {why}")]


def check_date_order(rec: dict, first: str, second: str, rule: str, label: str) -> list[Flag]:
    a, b = _date(rec.get(first)), _date(rec.get(second))
    out = []
    for f, d in ((first, a), (second, b)):
        if d and not 2000 <= d.year <= 2100:
            out.append(Flag(f, "date_range", f"year {d.year} is implausible"))
    if a and b and b < a:
        out.append(Flag(second, rule, label))
        out.append(Flag(first, rule, label))
    return out


def check_lines(rec: dict) -> list[Flag]:
    flags = []
    for i, ln in enumerate(rec.get("line_items") or []):
        qty, price, amt = _d(ln.get("qty")), _d(ln.get("unit_price")), _d(ln.get("amount"))
        involved = [line_path(i, "qty"), line_path(i, "unit_price"), line_path(i, "amount")]
        if None in (qty, price, amt):
            flags += [
                Flag(p, "missing", "line cell is empty")
                for p, v in zip(involved, (qty, price, amt), strict=True)
                if v is None
            ]
            # a row without its numbers is probably a wrapped description split off as its own line: the whole
            # list is suspect, because every line after it is shifted
            flags.append(
                Flag(line_path(i, "description"), "line_incomplete", "this row has no quantity, rate or amount")
            )
            flags.append(Flag("line_items", "line_incomplete", "a row of the items table is incomplete"))
            continue
        if qty <= 0 or price < 0 or amt < 0:
            flags += [Flag(p, "non_positive", "quantity, rate or amount is not positive") for p in involved]
        elif abs(qty * price - amt) > TOLERANCE:
            flags += [
                Flag(p, "line_amount_mismatch", f"qty x rate = {qty * price:.2f}, printed {amt:.2f}") for p in involved
            ]
    return flags


def check_subtotal(rec: dict) -> list[Flag]:
    amts = [_d(ln.get("amount")) for ln in rec.get("line_items") or []]
    sub = _d(rec.get("subtotal"))
    if sub is None or not amts or None in amts:
        return []
    s = sum(amts, Decimal(0))
    if abs(s - sub) > TOLERANCE:
        msg = f"lines add up to {s:.2f}, subtotal is {sub:.2f}"
        return [Flag("subtotal", "subtotal_mismatch", msg), Flag("line_items", "subtotal_mismatch", msg)]
    return []


def check_total(rec: dict) -> list[Flag]:
    sub, tax, total = _d(rec.get("subtotal")), _d(rec.get("gst_amount")), _d(rec.get("total"))
    if None in (sub, tax, total):
        return []
    if abs(sub + tax - total) > TOLERANCE:
        msg = f"subtotal + GST = {sub + tax:.2f}, total is {total:.2f}"
        return [Flag(f, "total_mismatch", msg) for f in ("total", "subtotal", "gst_amount")]
    return []


def check_tax(rec: dict) -> list[Flag]:
    sub, tax, rate = _d(rec.get("subtotal")), _d(rec.get("gst_amount")), _d(rec.get("gst_rate"))
    flags = []
    if rate is not None and float(rate) not in GST_SLABS:
        flags.append(Flag("gst_rate", "gst_rate_slab", f"{rate}% is not a GST slab"))
    if None not in (sub, tax, rate) and abs(sub * rate / 100 - tax) > TOLERANCE:
        msg = f"{rate}% of {sub:.2f} is {sub * rate / 100:.2f}, GST printed {tax:.2f}"
        flags += [Flag("gst_rate", "tax_mismatch", msg), Flag("gst_amount", "tax_mismatch", msg)]
    return flags


def check_currency(rec: dict) -> list[Flag]:
    c = rec.get("currency")
    if c in (None, ""):
        return [Flag("currency", "missing", "currency not found")]
    return [] if re.fullmatch(r"[A-Z]{3}", str(c)) else [Flag("currency", "currency_format", "not an ISO code")]


def check_positive(rec: dict, fields: tuple[str, ...]) -> list[Flag]:
    out = []
    for f in fields:
        v = _d(rec.get(f))
        if v is not None and v <= 0:
            out.append(Flag(f, "non_positive", "must be positive"))
    return out


def same_supplier(a: dict, b: dict) -> bool:
    ga, gb = gstin.normalize(a.get("supplier_gstin") or ""), gstin.normalize(b.get("supplier_gstin") or "")
    if ga and ga == gb:
        return True
    na, nb = squash(a.get("supplier_name") or ""), squash(b.get("supplier_name") or "")
    return bool(na) and na == nb


def check_supplier_not_buyer(rec: dict) -> list[Flag]:
    """The seller is never Larkfield itself: a model that picks the buyer block gets a valid GSTIN and a real name."""
    flags = []
    g = gstin.normalize(rec.get("supplier_gstin") or "")
    if g and g == gstin.normalize(settings.buyer_gstin):
        flags.append(Flag("supplier_gstin", "supplier_is_buyer", "this is the buyer's GSTIN, not the seller's"))
    sn, bk = squash(rec.get("supplier_name") or ""), squash(settings.buyer_name)
    if len(sn) >= 8 and (bk in sn or sn in bk):
        flags.append(Flag("supplier_name", "supplier_is_buyer", "this is the buyer's name, not the seller's"))
    return flags


def tax_type_in_text(text: str) -> str | None:
    """igst or cgst_sgst as printed on the document; None when it is not stated or both are mentioned."""
    t = text.casefold()
    split, igst = "cgst" in t or "sgst" in t, "igst" in t
    if split and not igst:
        return "cgst_sgst"
    if igst and not split:
        return "igst"
    return None


def check_tax_type(rec: dict, text: str) -> list[Flag]:
    """Same state as the buyer means CGST + SGST; another state means IGST. Uses the printed tax type."""
    g = gstin.normalize(rec.get("supplier_gstin") or "")
    kind = tax_type_in_text(text)
    if not kind or not re.match(r"\d{2}", g):
        return []
    same_state = g[:2] == gstin.state_code(settings.buyer_gstin)
    if same_state == (kind == "cgst_sgst"):
        return []
    want = "CGST + SGST" if same_state else "IGST"
    msg = f"supplier state {g[:2]} and the buyer's state {'match' if same_state else 'differ'}: expected {want}"
    return [Flag("supplier_gstin", "tax_type_state_mismatch", msg), Flag("gst_amount", "tax_type_state_mismatch", msg)]


def check_invoice_number_not_po(rec: dict) -> list[Flag]:
    num, marker = squash(rec.get("invoice_number") or ""), squash(settings.po_marker)
    if num and marker and marker in num:
        return [
            Flag("invoice_number", "invoice_number_is_po", "looks like a purchase-order number, not an invoice number")
        ]
    return []


def check_duplicate_invoice(rec: dict, prior: Iterable[dict]) -> list[Flag]:
    num = squash(rec.get("invoice_number") or "")
    if not num:
        return []
    for other in prior:
        if squash(other.get("invoice_number") or "") == num and same_supplier(rec, other):
            return [
                Flag("invoice_number", "duplicate_invoice_number", "same supplier and invoice number already stored")
            ]
    return []


def check_duplicate_awb(rec: dict, prior: Iterable[dict]) -> list[Flag]:
    awb = squash(rec.get("awb_number") or "")
    if awb and any(squash(o.get("awb_number") or "") == awb for o in prior):
        return [Flag("awb_number", "duplicate_awb", "this AWB number is already stored")]
    return []


def check_awb_format(rec: dict) -> list[Flag]:
    v = rec.get("awb_number")
    if v in (None, ""):
        return []
    return (
        []
        if AWB_RE.match(re.sub(r"[\s\-]", "", str(v)).upper())
        else [Flag("awb_number", "awb_format", "unexpected AWB format")]
    )


def validate_invoice(rec: dict, bad: Iterable[str], prior: Iterable[dict], text: str = "") -> list[Flag]:
    flags: list[Flag] = []
    flags += check_parse_errors(bad)
    flags += check_required("invoice", rec)
    flags += check_gstin(rec, "supplier_gstin")
    flags += check_date_order(
        rec, "invoice_date", "due_date", "due_before_invoice", "due date is before the invoice date"
    )
    flags += check_lines(rec)
    flags += check_subtotal(rec)
    flags += check_total(rec)
    flags += check_tax(rec)
    flags += check_currency(rec)
    flags += check_positive(rec, ("subtotal", "total"))
    flags += check_duplicate_invoice(rec, prior)
    flags += check_supplier_not_buyer(rec)
    flags += check_invoice_number_not_po(rec)
    flags += check_tax_type(rec, text)
    return _dedupe(flags)


def validate_delivery(rec: dict, bad: Iterable[str], prior: Iterable[dict], text: str = "") -> list[Flag]:
    flags: list[Flag] = []
    flags += check_parse_errors(bad)
    flags += check_required("delivery_note", rec)
    flags += check_gstin(rec, "courier_gstin")
    flags += check_date_order(
        rec, "ship_date", "delivery_date", "delivery_before_ship", "delivered before it was shipped"
    )
    flags += check_awb_format(rec)
    flags += check_positive(rec, ("packages", "weight_kg"))
    flags += check_duplicate_awb(rec, prior)
    return _dedupe(flags)


def _dedupe(flags: list[Flag]) -> list[Flag]:
    seen, out = set(), []
    for f in flags:
        if (f.field, f.rule) not in seen:
            seen.add((f.field, f.rule))
            out.append(f)
    return out


def validate(doc_type: str, rec: dict, bad: Iterable[str], prior: Iterable[dict], text: str = "") -> list[Flag]:
    return (validate_invoice if doc_type == "invoice" else validate_delivery)(rec, bad, prior, text)
