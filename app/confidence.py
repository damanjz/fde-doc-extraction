"""Step 4: confidence per field.

A field is LOW when any of these hold: a validation rule flagged it; its value is not found in the source
text as strictly as the scorer would compare it; or, for scans, the OCR confidence of the text it came from
is below the threshold.

"Found in the source" means:
- numbers: equal after normalisation (1,23,456.00 matches 123456); halves accepted for GST fields;
- dates: the ISO date is one of the dates written in the text (day first);
- ids and GSTINs: spaces ignored, punctuation and case kept, and the value must not be a piece of a longer
  token (389/26-27 is not found in BBW/389/26-27);
- names: case and spaces ignored, punctuation kept, and the value must be a whole phrase of the text (a text
  segment, one side of a colon or comma, or a two-row wrap), so Pearl City is not found in Pearl City
  Kitchenware Pvt Ltd;
- a due date must also sit on the same line as a due label.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from app.config import settings
from app.normalise import find_dates, number_tokens, squash
from app.schema import FIELDS, LINE_FIELDS, line_path
from app.textlayer import TextDoc
from app.validate import Flag

_INR = re.compile(r"(?i)\b(inr|rs\.?|rupees?)\b|₹")
_DUE_LABEL = re.compile(r"(?i)\bdue\b|payable by|pay by|bhugtan tarikh")
_GAP = re.compile(r"\s{2,}")
# the GSTIN grammar: 2-digit state, 10-character PAN, entity digit, Z, check character (any start inside a run)
_GSTIN_AT = re.compile(r"(?=(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]))")
_SPLIT = re.compile(r"\s*[:;|]\s*")
HALF_OK = {"gst_rate", "gst_amount"}  # CGST + SGST invoices print each half, not the total
_ID_EDGE = r"A-Za-z0-9/\-_"  # characters that would make a match a piece of a longer id
MAX_ID_LEN = 100
LABEL_WINDOW = 80


def key(s: str) -> str:
    """Name comparison key, the same as the scorer's: case and whitespace insensitive, punctuation kept."""
    return re.sub(r"\s+", "", s.casefold())


@dataclass
class _Row:
    parts: list[str]  # the cells of a text row
    cols: list[float]  # start column of each cell, empty when unknown
    blank_before: bool = False


COL_TOLERANCE = 2.5  # characters


@dataclass
class _Seg:
    conf: float
    squashed: str
    numbers: set[Decimal]
    dates: set[str]


class SourceIndex:
    """Fast lookups over the document text and, for OCR'd pages, per-segment confidences."""

    def __init__(self, td: TextDoc) -> None:
        self.text = td.text
        self.upper = self.text.upper()
        # GSTINs written in the text, even when OCR glued a label or the next word to them (GSTIN29AAB...M1Z4PAN)
        self.gstins = {m.group(1) for run in re.findall(r"[A-Z0-9]+", self.upper) for m in _GSTIN_AT.finditer(run)}
        self.squashed = squash(self.text)
        self.numbers = number_tokens(self.text)
        self.dates = find_dates(self.text)
        self.segs: list[_Seg] = []
        rows: list[_Row] = []
        for ln in td.lines:
            for text, conf in ln.segs:
                self.segs.append(_Seg(conf, squash(text), number_tokens(text), find_dates(text)))
            if ln.cols:
                parts, cols = [t.strip() for t, _ in ln.cols], [c for _, c in ln.cols]
            else:  # lines built without positions (tests): split on gaps, columns unknown
                parts, cols = [p.strip() for p in _GAP.split(ln.text.strip()) if p.strip()], []
            rows.append(_Row(parts, cols, ln.blank_before))
        self.phrases = self._phrases(rows)
        self._label_cache: dict[str, set[str]] = {}

    @staticmethod
    def _continued(a: _Row, b: _Row, k: int) -> bool:
        """Is cell k of table row `a` wrapped onto the next row `b`? True when `a` has several cells and `b` is a
        single cell directly below (no blank line) that starts in the same column."""
        return (
            len(a.parts) >= 3
            and len(b.parts) == 1
            and not b.blank_before
            and bool(a.cols)
            and bool(b.cols)
            and abs(a.cols[k] - b.cols[0]) <= COL_TOLERANCE
        )

    @classmethod
    def _phrases(cls, rows: list[_Row]) -> set[str]:
        out: set[str] = set()
        continued: set[tuple[int, int]] = set()
        for i, (a, b) in enumerate(zip(rows, rows[1:], strict=False)):
            for k in range(len(a.parts)):
                if cls._continued(a, b, k):
                    continued.add((i, k))
        for i, row in enumerate(rows):
            for k, seg in enumerate(row.parts):
                if (i, k) in continued:
                    continue  # the first half of a wrapped cell is not the whole value
                out.add(key(seg))
                for part in _SPLIT.split(seg):
                    out.add(key(part))
                comma = [c.strip() for c in seg.split(",")]
                for p in range(len(comma)):  # runs of comma-separated parts: "Chennai", "Ambattur, Chennai"
                    for q in range(p, min(len(comma), p + 3)):
                        out.add(key(",".join(comma[p : q + 1])))
        for i, k in continued:  # the wrapped cell as a whole
            out.add(key(rows[i].parts[k] + " " + rows[i + 1].parts[0]))
        for a, b in zip(rows, rows[1:], strict=False):  # a name wrapped over two rows without column information
            if not a.cols and len(b.parts) <= 3:
                out.update(key(x + " " + y) for x in a.parts for y in b.parts)
        out.discard("")
        return out

    def _label_dates(self, label: re.Pattern) -> set[str]:
        """Dates written on the same line, shortly after the label."""
        cache_key = label.pattern
        if cache_key not in self._label_cache:
            found: set[str] = set()
            for m in label.finditer(self.text):
                rest = self.text[m.end() :].split("\n", 1)[0][:LABEL_WINDOW]
                found |= find_dates(rest)
            self._label_cache[cache_key] = found
        return self._label_cache[cache_key]

    def due_dates_near_label(self) -> set[str]:
        return self._label_dates(_DUE_LABEL)

    @staticmethod
    def _num_match(value: object, pool: set[Decimal], half: bool) -> bool:
        try:
            d = Decimal(str(value))
        except Exception:
            return False
        return d in pool or (half and d / 2 in pool)

    @staticmethod
    def _id_found(value: str, text: str) -> bool:
        v = re.sub(r"\s+", "", value)
        if not v or len(v) > MAX_ID_LEN:
            return False
        pat = rf"(?<![{_ID_EDGE}])" + r"\s*".join(re.escape(c) for c in v) + rf"(?![{_ID_EDGE}])"
        return re.search(pat, text) is not None

    def has(self, kind: str, value: object, field: str = "") -> bool:
        if kind in ("amount", "qty", "rate", "int"):
            return self._num_match(value, self.numbers, field in HALF_OK)
        if kind == "date":
            return str(value) in self.dates
        s = str(value)
        if kind == "currency":
            # GST invoices are in rupees: a rupee marker or any GST wording is enough for INR
            if value == "INR":
                return bool(_INR.search(self.text) or "gst" in self.text.casefold())
            return squash(s) in self.squashed
        if not s.strip():
            return True
        if kind == "gstin":
            return re.sub(r"\s+", "", s.upper()) in self.gstins or self._id_found(s.upper(), self.upper)
        if kind == "id":
            return self._id_found(s, self.text)
        return key(s) in self.phrases  # name

    def ocr_conf(self, kind: str, value: object, field: str = "") -> float | None:
        """Best OCR confidence among the segments that contain the value (None if not an OCR'd source)."""
        confs = []
        for sg in self.segs:
            if kind in ("amount", "qty", "rate", "int"):
                hit = self._num_match(value, sg.numbers, field in HALF_OK)
            elif kind == "date":
                hit = str(value) in sg.dates
            elif kind == "currency":
                hit = False
            else:
                k = squash(str(value))
                hit = len(k) >= 3 and k in sg.squashed
            if hit:
                confs.append(sg.conf)
        return max(confs) if confs else None


def _paths(doc_type: str, rec: dict) -> list[tuple[str, str, object]]:
    out = [(name, kind, rec.get(name)) for name, kind in FIELDS[doc_type].items()]
    for i, ln in enumerate(rec.get("line_items") or []):
        out += [(line_path(i, n), k, ln.get(n)) for n, k in LINE_FIELDS.items()]
    return out


def assess(doc_type: str, rec: dict, flags: list[Flag], td: TextDoc) -> dict[str, dict]:
    """Return {path: {value, confidence, reasons}} for every field and line cell."""
    src = SourceIndex(td)
    by_path: dict[str, list[str]] = {}
    for f in flags:
        by_path.setdefault(f.field, []).append(f"validation:{f.rule}")
    out: dict[str, dict] = {}
    for path, kind, value in _paths(doc_type, rec):
        reasons = list(by_path.get(path, []))
        name = path.rsplit(".", 1)[-1]
        if value is not None and value != "":
            if not src.has(kind, value, name):
                reasons.append("not_in_source")
            else:
                if path == "due_date" and str(value) not in src.due_dates_near_label():
                    reasons.append("date_not_near_label")
                if td.is_scan:
                    c = src.ocr_conf(kind, value, name)
                    if c is not None and c < settings.ocr_low_conf:
                        reasons.append(f"ocr_low_confidence:{c:.2f}")
        elif path == "due_date" and src.due_dates_near_label():
            reasons.append("label_present_value_null")
        out[path] = {"value": value, "confidence": "low" if reasons else "high", "reasons": reasons}
    if "line_items" in by_path:  # list-level flag (lines do not add up, or no lines at all)
        out["line_items"] = {
            "value": len(rec.get("line_items") or []),
            "confidence": "low",
            "reasons": by_path["line_items"],
        }
    return out
