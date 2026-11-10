"""Measure how well rapidocr reads the generated scans.

    python scripts/ocr_accuracy.py [--ids scan_001,scan_002]

For each scan: character accuracy (1 - edit distance / reference length, whitespace ignored, rows in
reading order) and number-token recall, against the text that was drawn on the page. The scans are
clean synthetic renders, so these numbers are optimistic compared with real phone photos.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ocr  # noqa: E402
from app.textlayer import read_document  # noqa: E402
from data.generate import DOCS_DIR, build_specs  # noqa: E402

NUM = re.compile(r"\d[\d,]*\.?\d*")


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def reference_rows(spec) -> list[str]:
    """Rows of drawn text in reading order, grouped with the same row logic the OCR path uses."""
    rows: list[str] = []
    for page in spec.doc.texts():
        boxes = [([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], t, 1.0) for x0, y0, x1, y1, t in page]
        rows += [ln.text for ln in ocr.build_lines(boxes)]
    return rows


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="")
    args = ap.parse_args()
    want = set(args.ids.split(",")) if args.ids else None
    accs, recalls = [], []
    print(f"{'doc':10} {'layout':6} {'angle':>6} {'dpi':>4} {'char acc':>9} {'number recall':>14} {'mean conf':>10}")
    for sp in build_specs():
        if sp.source != "scan" or (want and sp.id not in want):
            continue
        td = read_document((DOCS_DIR / sp.truth["file"]).read_bytes())
        ref = norm("".join(reference_rows(sp)))
        hyp = norm("".join(ln.text for ln in td.lines))
        acc = max(0.0, 1 - edit_distance(ref, hyp) / len(ref))
        ref_nums = [n.rstrip(".,") for n in NUM.findall(" ".join(reference_rows(sp))) if len(n.rstrip(".,")) > 1]
        hyp_text = " ".join(ln.text for ln in td.lines)
        hyp_nums = set(n.rstrip(".,") for n in NUM.findall(hyp_text))
        rec = sum(n in hyp_nums for n in ref_nums) / max(len(ref_nums), 1)
        accs.append(acc)
        recalls.append(rec)
        print(
            f"{sp.id:10} {sp.layout:6} {sp.scan.angle:6.1f} {sp.scan.out_dpi:4} {acc:9.1%} {rec:14.1%} "
            f"{td.mean_ocr_conf:10.3f}"
        )
    print(
        f"mean char accuracy {statistics.fmean(accs):.1%}, min {min(accs):.1%}; "
        f"mean number recall {statistics.fmean(recalls):.1%}, min {min(recalls):.1%}"
    )


if __name__ == "__main__":
    main()
