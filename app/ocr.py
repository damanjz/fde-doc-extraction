"""OCR with rapidocr (models ship inside the wheel). Keeps line positions and confidences.

Scans are slightly rotated, so box coordinates are de-skewed (geometry only, the image is not
resampled) before boxes are grouped into rows.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

import numpy as np

_engine = None


def engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR  # lazy: heavy import

        _engine = RapidOCR()
    return _engine


@dataclass
class Seg:
    text: str
    conf: float
    x0: float
    x1: float
    col: float = 0.0  # x0 in character widths, to compare columns between rows


@dataclass
class OcrLine:
    text: str
    conf: float  # lowest segment confidence on the row
    y: float
    segs: list[Seg] = field(default_factory=list)


def read_boxes(img: np.ndarray) -> list[tuple[list[list[float]], str, float]]:
    result, _ = engine()(img)
    return [(b, t, float(s)) for b, t, s in (result or [])]


def skew_angle(boxes: list[tuple[list[list[float]], str, float]]) -> float:
    """Median angle (radians) of the top edge of wide boxes."""
    angles = []
    for b, _, _ in boxes:
        dx, dy = b[1][0] - b[0][0], b[1][1] - b[0][1]
        h = math.dist(b[0], b[3])
        if dx > 3 * h and h > 4:
            angles.append(math.atan2(dy, dx))
    return statistics.median(angles) if len(angles) >= 3 else 0.0


def build_lines(boxes: list[tuple[list[list[float]], str, float]]) -> list[OcrLine]:
    if not boxes:
        return []
    th = skew_angle(boxes)
    c, s = math.cos(th), math.sin(th)

    def rot(p: list[float]) -> tuple[float, float]:
        return p[0] * c + p[1] * s, -p[0] * s + p[1] * c

    items = []
    for b, text, conf in boxes:
        pts = [rot(p) for p in b]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        items.append((statistics.fmean(ys), min(ys), max(ys), min(xs), max(xs), text, conf))
    med_h = statistics.median(i[2] - i[1] for i in items)
    items.sort(key=lambda i: i[0])
    rows: list[list[tuple]] = []
    for it in items:
        if rows and abs(it[0] - statistics.fmean(r[0] for r in rows[-1])) < 0.55 * med_h:
            rows[-1].append(it)
        else:
            rows.append([it])
    char_w = statistics.median((i[4] - i[3]) / max(len(i[5]), 1) for i in items)
    lines = []
    for row in rows:
        row.sort(key=lambda i: i[3])
        parts, prev_x1 = [], None
        for it in row:
            if prev_x1 is not None:
                gap = int(round((it[3] - prev_x1) / char_w))
                parts.append(" " * max(1, min(gap, 12)))
            parts.append(it[5])
            prev_x1 = it[4]
        lines.append(
            OcrLine(
                text="".join(parts),
                conf=min(i[6] for i in row),
                y=statistics.fmean(r[0] for r in row),
                segs=[Seg(i[5], i[6], i[3], i[4], i[3] / char_w) for i in row],
            )
        )
    return lines


def ocr_image(img: np.ndarray) -> list[OcrLine]:
    return build_lines(read_boxes(img))
