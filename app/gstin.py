"""GSTIN format and checksum (Luhn mod 36 over the first 14 characters)."""

from __future__ import annotations

import re

CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# 2-digit state / UT codes in use (01-38; 97 other territory; 99 centre jurisdiction).
STATE_CODES = {f"{i:02d}" for i in range(1, 39)} | {"97", "99"}
GSTIN_RE = re.compile(r"^(\d{2})([A-Z]{5}\d{4}[A-Z])([1-9A-Z])Z([0-9A-Z])$")


def check_char(first14: str) -> str:
    """Checksum character for the first 14 characters of a GSTIN."""
    total = 0
    for i, ch in enumerate(first14):
        prod = CHARSET.index(ch) * (1 if i % 2 == 0 else 2)
        total += prod // 36 + prod % 36
    return CHARSET[(36 - total % 36) % 36]


def normalize(value: str) -> str:
    return re.sub(r"[\s\-]", "", value or "").upper()


def validate(value: str | None) -> tuple[bool, str]:
    """Return (ok, reason). reason is empty when ok."""
    if not value:
        return False, "missing"
    g = normalize(value)
    if len(g) != 15:
        return False, f"length {len(g)}, expected 15"
    m = GSTIN_RE.match(g)
    if not m:
        return False, "format does not match GSTIN pattern"
    if m.group(1) not in STATE_CODES:
        return False, f"unknown state code {m.group(1)}"
    if check_char(g[:14]) != g[14]:
        return False, "checksum mismatch"
    return True, ""


def make(state: str, pan: str, entity: str = "1") -> str:
    """Build a valid GSTIN (used by the data generator and tests)."""
    first14 = f"{state}{pan}{entity}Z"
    return first14 + check_char(first14)


def state_code(value: str) -> str:
    return normalize(value)[:2]
