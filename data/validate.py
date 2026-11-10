"""Check the generated dataset is complete and consistent. `python data/validate.py` prints OK or the problems."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

from app import gstin  # noqa: E402
from app import validate as v  # noqa: E402


def check() -> list[str]:
    problems: list[str] = []
    truths = {p.stem: json.loads(p.read_text("utf-8")) for p in sorted((ROOT / "truth").glob("*.json"))}
    if len(truths) != 50:
        problems.append(f"expected 50 documents, found {len(truths)}")
    kinds = Counter((t["type"], t["source"]) for t in truths.values())
    want = {("invoice", "pdf"): 30, ("delivery_note", "pdf"): 10, ("invoice", "scan"): 10}
    if dict(kinds) != want:
        problems.append(f"document mix {dict(kinds)} != {want}")
    seen: dict[str, list[dict]] = {"invoice": [], "delivery_note": []}
    for doc_id, t in truths.items():
        if not (ROOT / "docs" / t["file"]).exists():
            problems.append(f"{doc_id}: file {t['file']} missing")
        flags = v.validate(t["type"], t["fields"], [], seen[t["type"]])
        seen[t["type"]].append(t["fields"])
        got = {f.field for f in flags}
        if not t["planted"] and got:
            problems.append(f"{doc_id}: unplanted document fails validation on {sorted(got)}")
        for e in t["expected_flags"]:
            if e["field"] not in got:
                problems.append(f"{doc_id}: expected flag {e['field']} is not raised by the checks")
        if t["type"] == "invoice" and not t["planted"] and not gstin.validate(t["fields"]["supplier_gstin"])[0]:
            problems.append(f"{doc_id}: bad GSTIN on an unplanted invoice")
    planted = sum(bool(t["planted"]) for t in truths.values())
    if not 10 <= planted <= 14:
        problems.append(f"{planted} planted documents, expected about 10")
    return problems


if __name__ == "__main__":
    probs = check()
    print("OK" if not probs else "\n".join(probs))
    sys.exit(1 if probs else 0)
