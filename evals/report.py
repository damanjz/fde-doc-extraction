"""Markdown report from scored documents."""

from __future__ import annotations

from collections import Counter

from evals import scoring as sc


def _rate(a: int, b: int) -> str:
    return f"{a / b:.1%}" if b else "n/a"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _fmt(v: object) -> str:
    return "null" if v is None else str(v)


def _outcome_row(label: str, s: dict) -> list:
    n = s["cells"]
    return [
        label,
        _rate(s["correct"], n),
        f"{_rate(s['silent'], n)} ({s['silent']}/{n})",
        f"{s['docs_with_silent']}/{s['docs']}",
        s["auto"],
        s["correct_stp"],
        s["bad_stp"],
        s["touched"],
        f"{s['planted_passed']}/{s['planted_docs']}",
    ]


OUTCOME_HEADERS = [
    "Variant",
    "Field accuracy",
    "Silent error rate",
    "Docs with a silent error",
    "Auto-accepted",
    "Correct straight-through",
    "Bad straight-through",
    "Touched by a human",
    "Planted problems passed silently",
]


def render_report(
    docs: list[sc.DocScore], *, mock: bool, model: str, generated: str, injected: dict | None, partial: bool
) -> str:
    L: list[str] = []
    L += ["# Eval report" + (" (MOCK RUN)" if mock else ""), ""]
    if mock:
        L += [
            "> **This run used a mocked LLM** that returns the ground truth with a fixed list of injected errors. "
            "It proves the scoring works. It says nothing about the model. The live numbers are in `evals/report.md`.",
            "",
        ]
    if partial:
        L += ["> Partial run (subset of documents). Not comparable with a full run.", ""]
    by_type = Counter(d.doc_type for d in docs)
    L += [
        f"Generated {generated}. Model: `{model}`. {len(docs)} documents "
        f"({by_type['invoice']} invoices incl. {sum(d.source == 'scan' for d in docs)} scans, "
        f"{by_type['delivery_note']} delivery notes). Fresh SQLite file, fixed document order.",
        "",
    ]

    pipe, base = sc.variant_stats(docs, "pipeline"), sc.variant_stats(docs, "baseline")
    L += [
        "## Headline",
        "",
        "Silent error rate = wrong fields that were not flagged / all ground-truth fields. These are the errors that "
        "reach the spreadsheet.",
        "",
        _table(
            OUTCOME_HEADERS,
            [_outcome_row("Pipeline (validation + confidence)", pipe), _outcome_row("Baseline (raw extraction)", base)],
        ),
        "",
        "Document outcomes:",
        "",
        "- **Auto-accepted**: nothing for a person to look at (no flagged field, no document warning; a document type "
        "that was a guess counts as a human touch).",
        "- **Correct straight-through**: auto-accepted, every field right, no planted problem.",
        "- **Bad straight-through**: auto-accepted with a wrong field or a planted problem in it. This is what hurts.",
        "- **Touched by a human**: flagged, warned about, or failed.",
        "- **Planted problems passed silently**: planted documents that were auto-accepted.",
        "",
        "The baseline is the same extraction as the pipeline (same prompt, same reply, repair round included, same "
        "normalisation) without validation, confidence or warnings. Validation never changes a value, so field "
        "accuracy is identical by construction and the two differ only in what is flagged. Every wrong field of the "
        "baseline is silent and every document it extracts is auto-accepted.",
        "",
    ]

    L += ["## By document group", ""]
    rows = []
    for g in sc.GROUPS.values():
        sub = [d for d in docs if d.group == g]
        if not sub:
            continue
        p, b = sc.variant_stats(sub, "pipeline"), sc.variant_stats(sub, "baseline")
        label = g + (" (optimistic: clean synthetic scans)" if "scan" in g else "")
        rows.append(
            [
                label,
                len(sub),
                p["cells"],
                _rate(p["correct"], p["cells"]),
                _rate(p["silent"], p["cells"]),
                _rate(b["silent"], b["cells"]),
                f"{p['correct_stp']} / {p['bad_stp']} / {p['touched']}",
                f"{b['correct_stp']} / {b['bad_stp']} / {b['touched']}",
            ]
        )
    L += [
        _table(
            [
                "Group",
                "Docs",
                "Fields",
                "Accuracy",
                "Silent err, pipeline",
                "Silent err, baseline",
                "Pipeline: correct STP / bad STP / touched",
                "Baseline: correct STP / bad STP / touched",
            ],
            rows,
        ),
        "",
        "Scans are generated from clean synthetic renders, so the OCR numbers here are optimistic compared with "
        "real phone or flatbed scans.",
        "",
    ]

    la = sc.line_alignment(docs)
    L += [
        "## Line items: two ways to align",
        "",
        "The headline matches predicted lines to true lines by position, as the spec says: one dropped or split line "
        "shifts every line after it. Content alignment matches each true line to the predicted line with the same "
        "amount or description, so it shows how much of the line damage is only an order or offset problem.",
        "",
        _table(
            ["Alignment", "Line cells correct"],
            [
                ["By position (used in the headline)", sc.pct(la["pos_ok"], la["pos_n"])],
                ["By content", sc.pct(la["con_ok"], la["con_n"])],
            ],
        ),
        "",
    ]

    fq = sc.flag_quality(docs)
    L += [
        "## Flag quality (pipeline)",
        "",
        f"- Recall: {sc.pct(fq['wrong_flagged'], fq['wrong'])} of wrong fields were flagged.",
        f"- Precision: {sc.pct(fq['flagged_wrong'], fq['flagged'])} of flagged fields were wrong.",
        "- Cells of planted problems are left out of both (they are correctly extracted values that should be "
        "flagged; scored below). Documents the pipeline failed on are left out too. When the line list is flagged "
        "as a whole, every line cell counts as flagged.",
        "",
    ]
    dq = sc.doc_flag_quality(docs)
    L += [
        "Per document: "
        f"{sc.pct(dq['both'], dq['sent'])} of the documents sent to a person really had a problem (a wrong field, a "
        f"planted problem or an expected warning); {sc.pct(dq['both'], dq['problem_docs'])} of the documents with a "
        "problem were sent.",
        "",
    ]
    if fq["false_alarm_reasons"]:
        L += [
            "Why correct fields were flagged (false alarms):",
            "",
            _table(["Reason", "Fields"], [[k, v] for k, v in fq["false_alarm_reasons"].most_common(10)]),
            "",
        ]
    if fq["missed"]:
        L += [
            "Wrong fields that were not flagged (silent errors):",
            "",
            _table(["Doc", "Field", "Truth", "Extracted"], [[d, p, _fmt(t), _fmt(x)] for d, p, t, x in fq["missed"]]),
            "",
        ]
    else:
        L += ["No silent errors.", ""]

    pr = sc.planted_report(docs)
    states = Counter(r["state"] for r in pr["flags"])
    total = len(pr["flags"])
    L += [
        "## Planted problems",
        "",
        f"Expected flags: **{states['hit']}/{total} flagged by the expected rule**, "
        f"{states['other_reason']} flagged for another reason, {states['missed']} missed, "
        f"{states['failed']} on documents that failed. "
        + ("Every expected flag was raised." if states["hit"] == total else "The others are listed below."),
        "",
    ]
    shown = {"hit": "yes", "other_reason": "flagged, other reason", "missed": "NO", "failed": "NO (extraction failed)"}
    L += [
        _table(
            ["Doc", "Field", "Problem", "Flagged", "Reasons on the field"],
            [
                [r["doc"], r["field"], r["reason"], shown[r["state"]], ", ".join(r["matched"]) or "-"]
                for r in pr["flags"]
            ],
        ),
        "",
    ]
    for r in pr["warnings"]:
        L += [
            f"- {r['doc']}: document warning `{r['warning']}` {'raised' if r['hit'] else 'NOT raised'}; "
            f"all extracted values {'correct' if r['values_ok'] else 'NOT all correct'}."
        ]
    L += [""]
    bad = [r for r in pr["flags"] if r["state"] != "hit"]
    if bad:
        L += ["Not flagged by the expected rule:", ""] + [
            f"- {m['doc']} {m['field']} ({m['reason']}): {m['state']}" for m in bad
        ]
        L += [""]

    L += ["## Accuracy per field", ""]
    rows = []
    for r in sc.per_field(docs):
        rows.append([r["field"], r["n"], _rate(r["ok"], r["n"]), r["flagged_wrong"], r["silent"]])
    L += [_table(["Field", "Cells", "Accuracy", "Wrong and flagged", "Wrong and silent"], rows), ""]

    lp, lb = sc.latency(docs, "pipeline"), sc.latency(docs, "baseline")
    L += [
        "## Latency per document",
        "",
        _table(
            ["Variant", "p50", "p95"],
            [
                ["Pipeline (text layer or OCR + LLM + checks)", f"{lp['p50']:.1f}s", f"{lp['p95']:.1f}s"],
                ["Baseline (text layer or OCR + LLM, no checks)", f"{lb['p50']:.1f}s", f"{lb['p95']:.1f}s"],
            ],
        ),
        "",
    ]
    for g in sc.GROUPS.values():
        sub = [d for d in docs if d.group == g]
        if sub:
            x = sc.latency(sub, "pipeline")
            L += [f"- {g}: p50 {x['p50']:.1f}s, p95 {x['p95']:.1f}s"]
    if mock:
        L += ["", "Mock latencies cover text extraction, OCR and the checks only; there is no model call."]
    L += [""]

    failed = [d.id for d in docs if d.failed_pipeline]
    repaired = [d.id for d in docs if d.repaired]
    L += [
        "## Other",
        "",
        f"- Document type recognised correctly: {sc.pct(sum(d.type_ok for d in docs), len(docs))}.",
        f"- Failed extractions: {len(failed)}" + (f" ({', '.join(failed)})" if failed else "") + ".",
        f"- First reply was not valid JSON and the repair round rescued it: {len(repaired)}"
        + (f" ({', '.join(repaired)})" if repaired else "")
        + ".",
    ]
    if injected is not None:
        applied = {k: v for k, v in injected.items() if v}
        L += [
            "",
            "## Mock: injected errors",
            "",
            "Errors the mock injected (some are caught, some are silent on purpose):",
            "",
        ]
        L += [f"- {k}: {', '.join(v)}" for k, v in sorted(applied.items())]
    return "\n".join(L) + "\n"
