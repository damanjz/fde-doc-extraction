"""A fake LLM for tests and for proving the eval scoring works without the GPU.

It returns the ground truth with a fixed list of injected errors. Some are catchable (a value that is not
in the source, a sum that does not add up, the buyer's GSTIN) and some are silent on purpose (quantity and rate
swapped, two lines in the wrong order), so the report shows both caught and escaped errors. This is NOT a
model result.
"""

from __future__ import annotations

import copy
import json

from app.llm import LLMResponse
from data.common import BUYER_GSTIN

# doc id -> list of (kind, path). Kinds are applied to the truth `fields` before the reply is written.
INJECTED: dict[str, list[tuple[str, str]]] = {
    "inv_004": [("rename", "supplier_name")],  # not in source -> flagged
    "inv_007": [("digits", "line_items[0].amount")],  # amount changes -> line mismatch flagged
    "inv_009": [("buyer_gstin", "supplier_gstin")],  # valid and in the text -> caught by the buyer rule
    "inv_010": [("drop_last_line", "line_items")],  # subtotal no longer adds up -> flagged
    "inv_016": [("swap_day_month", "invoice_date")],  # not in source -> flagged
    "inv_013": [("swap_qty_price", "line_items")],  # product is unchanged, both numbers are in the text -> SILENT
    "inv_018": [("swap_lines", "line_items")],  # lines 0 and 1 swapped: sums unchanged -> SILENT (by position)
    "inv_024": [("due_eq_invoice", "due_date")],  # a real date, but not next to a due label -> flagged
    "dn_003": [("rename", "received_by")],  # not in source -> flagged
    "scan_005": [("null", "total")],  # missed field -> flagged
    "scan_009": [("ocr_letter", "line_items[0].qty")],  # unparseable -> flagged
    "scan_010": [("digits", "subtotal")],  # not in source -> flagged
}
BAD_JSON_FIRST = {"inv_002", "scan_007"}  # first reply is cut off; the repair round returns the full reply
FORMAT_VARIANTS = {"inv_001", "inv_006", "inv_011", "inv_013", "inv_018", "inv_025", "scan_003", "scan_008"}


def _set(fields: dict, path: str, value) -> None:
    if path.startswith("line_items["):
        idx, name = path[len("line_items[") :].split("].")
        fields["line_items"][int(idx)][name] = value
    else:
        fields[path] = value


def _get(fields: dict, path: str):
    if path.startswith("line_items["):
        idx, name = path[len("line_items[") :].split("].")
        return fields["line_items"][int(idx)][name]
    return fields[path]


def _lakh(x: float) -> str:
    s = f"{x:.2f}"
    whole, frac = s.split(".")
    head, tail = whole[:-3], whole[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail] if parts else [tail]) + "." + frac


def _variant(fields: dict) -> dict:
    """Write amounts as printed (lakh grouping, rupee sign) and dates as dd-Mon-yyyy: normalisation must cope."""
    mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    out = copy.deepcopy(fields)
    for k in ("subtotal", "gst_amount", "total"):
        if isinstance(out.get(k), float):
            out[k] = "₹ " + _lakh(out[k])
    for k in ("invoice_date", "due_date"):
        if out.get(k):
            y, m, d = out[k].split("-")
            out[k] = f"{d}-{mon[int(m) - 1]}-{y}"
    for ln in out.get("line_items", []):
        ln["amount"] = _lakh(ln["amount"])
    return out


def inject(doc_id: str, fields: dict) -> list[str]:
    """Apply the planned errors in place. Returns the paths that were changed."""
    applied = []
    for kind, path in INJECTED.get(doc_id, []):
        cur = None if kind in ("drop_last_line", "swap_lines", "swap_qty_price") else _get(fields, path)
        if kind == "rename":
            _set(fields, path, cur.replace("a", "e", 1) if "a" in cur else cur + "x")
        elif kind == "digits":
            _set(fields, path, round(cur * 1.1 + 13, 2))
        elif kind == "buyer_gstin":
            _set(fields, path, BUYER_GSTIN)
        elif kind == "drop_last_line":
            fields["line_items"].pop()
        elif kind == "swap_lines":
            fields["line_items"][0], fields["line_items"][1] = fields["line_items"][1], fields["line_items"][0]
        elif kind == "swap_qty_price":
            ln = next(x for x in fields["line_items"] if x["qty"] != x["unit_price"])
            ln["qty"], ln["unit_price"] = ln["unit_price"], ln["qty"]
        elif kind == "swap_day_month":
            y, m, d = cur.split("-")
            day = int(d)
            _set(
                fields, path, f"{y}-{d}-{m}" if day <= 12 and d != m else f"{y}-{m}-{day + (1 if day < 28 else -1):02d}"
            )
        elif kind == "due_eq_invoice":
            _set(fields, path, fields["invoice_date"] if fields.get("due_date") else None)
        elif kind == "null":
            _set(fields, path, None)
        elif kind == "ocr_letter":
            _set(fields, path, f"{int(cur)}O")
        applied.append(path)
    return applied


class MockLLM:
    """Callable with the same shape as app.llm.chat. Set `.current` to the document id before each document."""

    def __init__(self, truths: dict[str, dict]) -> None:
        self.truths = truths
        self.current: str | None = None
        self.calls = 0
        self.injected: dict[str, list[str]] = {}

    def __call__(self, messages: list[dict]) -> LLMResponse:
        assert sum(m["role"] == "system" for m in messages) == 1, "one system message per request"
        self.calls += 1
        doc_id = self.current
        fields = copy.deepcopy(self.truths[doc_id]["fields"])
        self.injected[doc_id] = inject(doc_id, fields)
        if doc_id in FORMAT_VARIANTS and "line_items" in fields:
            fields = _variant(fields)
        full = json.dumps(fields)
        repair_turn = len(messages) > 2
        if doc_id in BAD_JSON_FIRST and not repair_turn:
            return LLMResponse(content=full[: len(full) // 2], latency_ms=5, model="mock")
        return LLMResponse(content=full, latency_ms=5, model="mock")
