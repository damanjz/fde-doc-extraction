"""Scoring: cell-level correctness, flags, silent errors, document outcomes, planted problems, latency.

Pure functions. The denominator of every field rate is the number of ground-truth cells (header fields
plus 4 cells per true line item), fixed per document: extra predicted lines add errors, never denominator.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from app.normalise import name_key
from app.schema import FIELDS, LINE_FIELDS, line_path

GROUPS = {
    ("invoice", "pdf"): "Invoices, text PDF",
    ("invoice", "scan"): "Invoices, scans (OCR)",
    ("delivery_note", "pdf"): "Delivery notes, text PDF",
}
NUMERIC = {"amount": 2, "rate": 2, "qty": 3, "int": 0}


@dataclass
class Cell:
    path: str
    name: str  # field name for aggregation (line cells: line_items.<cell>)
    truth: object
    pred: object
    correct: bool
    flagged: bool
    reasons: list[str] = field(default_factory=list)
    planted: bool = False  # part of a planted problem: scored separately
    extra: bool = False  # a cell of a predicted line that has no true line: an error, not part of the denominator


def _is_number(v: object) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def equal(kind: str, truth: object, pred: object) -> bool:
    """Exact after normalisation. A numeric field holding a string (or anything but a number) is wrong."""
    if truth is None or pred is None:
        return truth is None and pred is None
    if kind in NUMERIC:
        if not (_is_number(truth) and _is_number(pred)):
            return False
        try:
            return round(Decimal(str(truth)), NUMERIC[kind]) == round(Decimal(str(pred)), NUMERIC[kind])
        except Exception:
            return False
    if not isinstance(truth, str) or not isinstance(pred, str):
        return False
    if kind == "name":
        return name_key(truth) == name_key(pred)
    return truth.strip() == pred.strip()


def planted_paths(truth: dict) -> set[str]:
    out: set[str] = set()
    for e in truth.get("expected_flags", []):
        out.add(e["field"])
        out.update(e.get("related", []))
    return out


def cells_for(truth: dict, record: dict | None, low: dict[str, list[str]], failed_flag: bool) -> list[Cell]:
    """Compare a canonical record with the truth, cell by cell. Line items are aligned by position.

    `low` maps flagged paths to reasons. When the line list is flagged as a whole ("line_items" in low, for
    example the lines do not add up to the subtotal) every line cell counts as flagged. A true line missing
    from the output counts its cells as wrong. A predicted line with no true line counts as extra cells
    (wrong, outside the denominator). `failed_flag` marks every cell of a document the pipeline could not
    extract as flagged: a failure is visible, not silent.
    """
    doc_type = truth["type"]
    tf = truth["fields"]
    planted = planted_paths(truth)
    list_flag = "line_items" in low
    cells: list[Cell] = []

    def add(path: str, name: str, kind: str, t: object, p: object, *, line: bool = False, extra: bool = False) -> None:
        flagged = failed_flag or path in low or (line and list_flag)
        ok = record is not None and not extra and equal(kind, t, p)
        # a planted problem on the line list (subtotal does not add up) involves every line cell
        is_planted = path in planted or (line and "line_items" in planted)
        cells.append(Cell(path, name, t, p, ok, flagged, low.get(path, []), is_planted, extra))

    for name, kind in FIELDS[doc_type].items():
        add(name, name, kind, tf.get(name), record.get(name) if record else None)
    if doc_type == "invoice":
        t_lines = tf.get("line_items", [])
        p_lines = (record or {}).get("line_items") or []
        for i in range(max(len(t_lines), len(p_lines))):
            for name, kind in LINE_FIELDS.items():
                t = t_lines[i].get(name) if i < len(t_lines) else None
                p = p_lines[i].get(name) if i < len(p_lines) else None
                add(line_path(i, name), f"line_items.{name}", kind, t, p, line=True, extra=i >= len(t_lines))
    return cells


# --- content-aligned line scoring ------------------------------------------------------------------


def _line_match_score(t: dict, p: dict) -> int:
    s = 0
    s += 2 * equal("amount", t.get("amount"), p.get("amount"))
    s += 2 * equal("name", t.get("description"), p.get("description"))
    s += equal("qty", t.get("qty"), p.get("qty")) + equal("amount", t.get("unit_price"), p.get("unit_price"))
    return s


def align_lines(t_lines: list[dict], p_lines: list[dict]) -> list[int | None]:
    """For each true line, the index of the predicted line with the best content match (each used once)."""
    used: set[int] = set()
    out: list[int | None] = []
    for t in t_lines:
        best, best_score = None, 1  # a match needs at least an equal amount or description
        for j, p in enumerate(p_lines):
            if j in used:
                continue
            sc = _line_match_score(t, p)
            if sc > best_score:
                best, best_score = j, sc
        if best is not None:
            used.add(best)
        out.append(best)
    return out


def line_cells_content(truth: dict, record: dict | None) -> list[bool]:
    """Correctness of every true line cell when lines are matched by content instead of position."""
    if truth["type"] != "invoice":
        return []
    t_lines = truth["fields"].get("line_items", [])
    p_lines = (record or {}).get("line_items") or [] if record else []
    out: list[bool] = []
    for t, j in zip(t_lines, align_lines(t_lines, p_lines), strict=True):
        for name, kind in LINE_FIELDS.items():
            out.append(j is not None and equal(kind, t.get(name), p_lines[j].get(name)))
    return out


@dataclass
class DocScore:
    id: str
    doc_type: str
    source: str
    group: str
    planted: list[str]
    pipeline: list[Cell]
    baseline: list[Cell]
    failed_pipeline: bool
    failed_baseline: bool
    warnings: list[str]
    expected_warnings: list[str]
    expected_flags: list[dict]
    low: dict[str, list[str]]
    type_ok: bool
    t_pipeline_ms: int
    t_baseline_ms: int
    line_content: list[bool] = field(default_factory=list)
    repaired: bool = False

    @property
    def flagged_paths(self) -> set[str]:
        return set(self.low)


def score_doc(truth: dict, res: dict) -> DocScore:
    """res: {record, low: {path: reasons}, warnings, doc_type, timings, failed, repaired}

    The baseline is the same extraction (including the repair round) without validation or confidence, so it
    shares the record and has no flags and no warnings.
    """
    record = res.get("record")
    failed = record is None
    low = res.get("low", {})
    tm = res.get("timings", {})
    kind = "scan" if truth["source"] == "scan" else "pdf"
    return DocScore(
        id=truth["id"],
        doc_type=truth["type"],
        source=kind,
        group=GROUPS[(truth["type"], kind)],
        planted=truth["planted"],
        pipeline=cells_for(truth, record, low, failed),
        baseline=cells_for(truth, record, {}, False),
        failed_pipeline=failed,
        failed_baseline=failed,
        warnings=res.get("warnings", []),
        expected_warnings=truth.get("expected_warnings", []),
        expected_flags=truth.get("expected_flags", []),
        low=low,
        type_ok=res.get("doc_type") == truth["type"],
        t_pipeline_ms=tm.get("total_ms", 0),
        t_baseline_ms=tm.get("text_ms", 0) + tm.get("llm_ms", 0),
        line_content=line_cells_content(truth, record),
        repaired=bool(res.get("repaired")),
    )


# --- aggregation -----------------------------------------------------------------------------------


def pct(a: int, b: int) -> str:
    return f"{a}/{b} ({a / b:.1%})" if b else "n/a"


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    k = (len(v) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def touched(d: DocScore, variant: str) -> bool:
    """Does a person have to look at this document? The baseline has no flags: only failures are visible."""
    if variant == "baseline":
        return d.failed_baseline
    return d.failed_pipeline or bool(d.low) or bool(d.warnings)  # any warning, document type uncertain included


def variant_stats(docs: list[DocScore], variant: str) -> dict:
    """Field and document outcomes for `pipeline` or `baseline`.

    Document outcomes: auto-accepted (no human touch); correct straight-through (auto-accepted, every field
    right, no planted problem); bad straight-through (auto-accepted with a wrong field or a planted problem);
    touched by a human; planted problems that went through unflagged.
    """
    s = Counter()
    for d in docs:
        cells = d.pipeline if variant == "pipeline" else d.baseline
        failed = d.failed_pipeline if variant == "pipeline" else d.failed_baseline
        s["docs"] += 1
        s["planted_docs"] += bool(d.planted)
        doc_silent = doc_wrong = 0
        for c in cells:
            if not c.extra:
                s["cells"] += 1
                s["correct"] += c.correct
            if not c.correct:
                s["wrong"] += 1
                doc_wrong += 1
                if c.flagged:
                    s["wrong_flagged"] += 1
                elif not failed:
                    s["silent"] += 1
                    doc_silent += 1
        s["docs_with_silent"] += doc_silent > 0
        if touched(d, variant):
            s["touched"] += 1
            continue
        s["auto"] += 1
        if doc_wrong == 0 and not d.planted:
            s["correct_stp"] += 1
        else:
            s["bad_stp"] += 1
        s["planted_passed"] += bool(d.planted)
    return dict(s) | {
        k: s[k]
        for k in (
            "docs",
            "cells",
            "correct",
            "wrong",
            "silent",
            "touched",
            "auto",
            "correct_stp",
            "bad_stp",
            "planted_passed",
            "planted_docs",
            "docs_with_silent",
            "wrong_flagged",
        )
    }


def line_alignment(docs: list[DocScore]) -> dict:
    """Line-cell accuracy with lines matched by position (the headline scoring) and by content."""
    pos_ok = pos_n = con_ok = con_n = 0
    for d in docs:
        pos = [c for c in d.pipeline if c.name.startswith("line_items.") and not c.extra]
        pos_n += len(pos)
        pos_ok += sum(c.correct for c in pos)
        con_n += len(d.line_content)
        con_ok += sum(d.line_content)
    return {"pos_ok": pos_ok, "pos_n": pos_n, "con_ok": con_ok, "con_n": con_n}


def reason_key(r: str) -> str:
    return r if r.startswith("validation:") else r.split(":")[0]


def doc_flag_quality(docs: list[DocScore]) -> dict:
    """The same question per document: of the documents sent to a person, how many really had a problem (a wrong
    field, a planted problem or an expected warning), and how many problem documents were sent. Cell precision
    is harsh on a flagged line list (every line cell counts as flagged); this view is not."""
    problem = sent = both = 0
    for d in docs:
        has_problem = d.failed_pipeline or any(not c.correct for c in d.pipeline) or bool(d.planted)
        is_sent = touched(d, "pipeline")
        problem += has_problem
        sent += is_sent
        both += has_problem and is_sent
    return {"problem_docs": problem, "sent": sent, "both": both}


def flag_quality(docs: list[DocScore]) -> dict:
    """Recall and precision of pipeline flags, planted-problem cells left out, failed documents left out."""
    wrong = wrong_flagged = flagged = flagged_wrong = 0
    false_alarm_reasons: Counter[str] = Counter()
    missed: list[tuple[str, str, object, object]] = []
    for d in docs:
        if d.failed_pipeline:
            continue
        for cell in d.pipeline:
            if cell.planted:
                continue
            if not cell.correct:
                wrong += 1
                wrong_flagged += cell.flagged
                if not cell.flagged:
                    missed.append((d.id, cell.path, cell.truth, cell.pred))
            if cell.flagged:
                flagged += 1
                flagged_wrong += not cell.correct
                if cell.correct:
                    for r in cell.reasons or ["line list flagged"]:
                        false_alarm_reasons[reason_key(r)] += 1
    return {
        "wrong": wrong,
        "wrong_flagged": wrong_flagged,
        "flagged": flagged,
        "flagged_wrong": flagged_wrong,
        "false_alarm_reasons": false_alarm_reasons,
        "missed": missed,
    }


def planted_report(docs: list[DocScore]) -> dict:
    """Each expected flag is `hit` (field flagged by the expected rule), `other_reason` (field flagged, but not
    for the expected reason: e.g. an invented date that is merely not in the text) or `missed`."""
    rows, warn_rows = [], []
    for d in docs:
        for e in d.expected_flags:
            reasons = d.low.get(e["field"])
            if d.failed_pipeline:
                state = "failed"
            elif reasons is None:
                state = "missed"
            elif f"validation:{e['reason']}" in reasons:
                state = "hit"
            else:
                state = "other_reason"
            rows.append(
                {"doc": d.id, "field": e["field"], "reason": e["reason"], "state": state, "matched": reasons or []}
            )
        for w in d.expected_warnings:
            warn_rows.append(
                {"doc": d.id, "warning": w, "hit": w in d.warnings, "values_ok": all(c.correct for c in d.pipeline)}
            )
    return {"flags": rows, "warnings": warn_rows}


def per_field(docs: list[DocScore]) -> list[dict]:
    agg: dict[str, dict] = defaultdict(lambda: {"n": 0, "ok": 0, "flagged_wrong": 0, "silent": 0})
    for d in docs:
        for cell in d.pipeline:
            a = agg[cell.name]
            if not cell.extra:
                a["n"] += 1
                a["ok"] += cell.correct
            if not cell.correct and not d.failed_pipeline:
                a["flagged_wrong" if cell.flagged else "silent"] += 1
    return [{"field": k, **v} for k, v in sorted(agg.items())]


def latency(docs: list[DocScore], variant: str) -> dict:
    vals = [(d.t_pipeline_ms if variant == "pipeline" else d.t_baseline_ms) / 1000 for d in docs]
    return {"p50": statistics.median(vals) if vals else 0.0, "p95": percentile(vals, 0.95)}
