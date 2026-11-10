"""Turn whatever the LLM returned into canonical values: ISO dates, numbers, upper-case GSTINs.

Values that cannot be parsed are kept as the raw string and their path is reported, so validation can
flag them instead of silently dropping them.
"""

from __future__ import annotations

import math
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from app.schema import FIELDS, LINE_FIELDS, line_path

_MONTHS = {
    m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)
}
_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_NUMERIC = re.compile(r"(?<![\d/.\-])(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4}|\d{2})(?![\d/])")
_NAMED = re.compile(r"(?<!\d)(\d{1,2})[ \-/.]([A-Za-z]{3,9})\.?[ \-/.,]+(\d{4}|\d{2})(?!\d)")
MAX_NUMBER = Decimal(10) ** 13  # anything larger is not an amount on a supplier invoice
MAX_STR = 1000
_CURRENCY_WORDS = re.compile(r"(?i)(inr|rs\.?|₹|rupees?)")
_NUM_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?")
# OCR often reads the commas of 1,23,456.00 as dots (1.23.456.00): groups of 2 or 3 digits joined by dots or commas,
# then a 2-digit decimal part. The last separator is the decimal point, the others are grouping.
_REGROUPED = re.compile(r"(?<![\d.,])\d{1,3}(?:[.,]\d{2,3})+[.,]\d{2}(?!\d)")


def _mk_date(y: int, m: int, d: int) -> date | None:
    if y < 100:
        y += 2000
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_dates(text: str) -> set[str]:
    """Every date written in the text (ISO, dd/mm/yyyy, dd-Mon-yyyy ...) as ISO strings. Day first."""
    out: set[date | None] = set()
    for m in _ISO.finditer(text):
        out.add(_mk_date(int(m[1]), int(m[2]), int(m[3])))
    for m in _NUMERIC.finditer(text):
        out.add(_mk_date(int(m[3]), int(m[2]), int(m[1])))
    for m in _NAMED.finditer(text):
        mon = _MONTHS.get(m[2][:3].lower())
        if mon:
            out.add(_mk_date(int(m[3]), mon, int(m[1])))
    return {d.isoformat() for d in out if d}


def parse_date(v: object) -> date | None:
    if isinstance(v, date):
        return v
    if not isinstance(v, str):
        return None
    s = v.strip()
    found = find_dates(s)
    # the whole string must be the date, not a date inside other text
    if len(found) == 1 and len(re.sub(r"[\d\-/. ,A-Za-z]", "", s)) == 0 and len(s) <= 20:
        return date.fromisoformat(next(iter(found)))
    return None


def parse_decimal(v: object) -> Decimal | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, float) and not math.isfinite(v):
        return None  # NaN and Infinity are valid JSON to Python's parser but never valid amounts
    if isinstance(v, int | float):
        d = Decimal(str(v))
    elif isinstance(v, str):
        s = _CURRENCY_WORDS.sub("", v).replace(",", "").replace(" ", "").strip()
        neg = s.startswith("(") and s.endswith(")")
        s = s.strip("()")
        if not re.fullmatch(r"-?[0-9]+(\.[0-9]+)?", s):  # ASCII digits only, no exponent
            return None
        try:
            d = Decimal(s)
        except InvalidOperation:
            return None
        d = -d if neg else d
    else:
        return None
    return d if d.is_finite() and abs(d) <= MAX_NUMBER else None


def number_tokens(text: str) -> set[Decimal]:
    """Numbers written in the text, lakh or western grouping, as exact Decimals.

    Also the regrouped reading of a token whose thousands separators were misread as dots (22.158.00 is 22158.00).
    """
    out = set()
    for tok in _NUM_TOKEN.findall(text):
        try:
            out.add(Decimal(tok.replace(",", "")))
        except InvalidOperation:
            continue
    for tok in _REGROUPED.findall(text):
        digits = re.sub(r"[.,]", "", tok)
        out.add(Decimal(digits[:-2] + "." + digits[-2:]))
    return out


def squash(s: str) -> str:
    """Case-insensitive alphanumerics only: used to compare names and ids across spacing and punctuation."""
    return "".join(ch for ch in s.casefold() if ch.isalnum())


def name_key(s: str | None) -> str:
    """Scoring key for names: case and whitespace insensitive (punctuation still matters)."""
    return re.sub(r"\s+", "", (s or "").casefold())


def _clean_str(v: object) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def canonical_value(kind: str, v: object) -> tuple[object, bool]:
    """Return (canonical value, ok). ok is False when a non-null value could not be parsed."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None, True
    if kind in ("name", "id"):
        if isinstance(v, float) and math.isfinite(v) and v == int(v):
            v = int(v)
        s = _clean_str(v)
        if s is not None and len(s) > MAX_STR:
            return s[:MAX_STR], False
        return s, True
    if kind == "gstin":
        return re.sub(r"[\s\-]", "", str(v)).upper(), True
    if kind == "currency":
        s = str(v).strip()
        return ("INR" if _CURRENCY_WORDS.fullmatch(s) else s.upper()), True
    if kind == "date":
        d = parse_date(v)
        return (d.isoformat(), True) if d else (str(v).strip(), False)
    dec = parse_decimal(v)
    if dec is None:
        return str(v).strip(), False
    if kind == "int":
        return (int(dec), True) if dec == int(dec) else (float(dec), False)
    places = {"amount": 2, "rate": 2, "qty": 3}[kind]
    return float(round(dec, places)), True


def canonical(doc_type: str, raw: dict) -> tuple[dict, set[str]]:
    """Canonical record in the same shape as the ground-truth `fields`, plus paths that failed to parse."""
    rec: dict = {}
    bad: set[str] = set()
    for name, kind in FIELDS[doc_type].items():
        rec[name], ok = canonical_value(kind, raw.get(name))
        if not ok:
            bad.add(name)
    if doc_type == "invoice":
        items = raw.get("line_items")
        if not isinstance(items, list):
            items = []
            if raw.get("line_items") is not None:
                bad.add("line_items")
        lines = []
        for it in items:
            if not isinstance(it, dict):
                bad.add("line_items")
                continue
            line = {}
            for name, kind in LINE_FIELDS.items():
                line[name], ok = canonical_value(kind, it.get(name))
                if not ok:
                    bad.add(line_path(len(lines), name))
            lines.append(line)
        rec["line_items"] = lines
    return rec, bad
