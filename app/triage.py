"""Code-side triage of the source text: document type and injected instructions."""

from __future__ import annotations

import re

_DN = {
    "delivery note": 3,
    "proof of delivery": 3,
    "docket": 2,
    "awb": 2,
    "consignee": 1,
    "consignor": 1,
    "received by": 1,
    "signed by": 1,
    "pieces": 1,
    "no. of packages": 1,
}
_INV = {
    "tax invoice": 3,
    "invoice no": 2,
    "invoice #": 2,
    "bill no": 2,
    "bill sankhya": 2,
    "hsn": 2,
    "igst": 2,
    "cgst": 2,
    "sgst": 2,
    "grand total": 1,
    "taxable": 1,
    "net payable": 1,
    "total due": 1,
    "amount excl": 1,
    "subtotal": 1,
    "gstin": 1,
}
_INJECTION = re.compile(
    r"(?i)\b(ignore|disregard|forget|override)\b[^.\n]{0,60}\b(above|previous|prior|instructions?|rules?|table|"
    r"total|prompt|values?)\b|\bsystem prompt\b|\byou are (an? )?(ai|assistant|language model)\b|"
    r"\b(set|change|make)\b[^.\n]{0,30}\btotal\b[^.\n]{0,20}\bto\s+(zero|nil|0|rs\.?\s*\d)|"
    r"\btotal\s+(is|=)\s*(0|zero|nil)\b"
)


def classify(text: str) -> tuple[str, bool]:
    """Return (doc_type, confident). Defaults to invoice when nothing matches."""
    t = text.casefold()
    dn = sum(w for k, w in _DN.items() if k in t)
    inv = sum(w for k, w in _INV.items() if k in t)
    if dn == 0 and inv == 0:
        return "invoice", False
    return ("delivery_note" if dn > inv else "invoice"), abs(dn - inv) >= 2


def injection_warning(text: str) -> bool:
    return bool(_INJECTION.search(text))
