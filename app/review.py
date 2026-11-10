"""Review screen (plain HTML, no CDN) and the CSV exports."""

from __future__ import annotations

import csv
import io
import json
from html import escape

from app.normalise import canonical_value
from app.schema import FIELDS, LINE_FIELDS, kind_of, line_path

STYLE = """
:root{--bg:#f6f4ef;--ink:#1d1b18;--mute:#6b665d;--line:#ddd8cc;--card:#fffdf8;--low:#fff1cf;--lowb:#d9a21b;--ok:#2d6a4f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,Segoe UI,sans-serif}
header{padding:14px 24px;border-bottom:1px solid var(--line);background:var(--card);display:flex;gap:14px;align-items:center;flex-wrap:wrap}
header h1{font-size:17px;margin:0;font-weight:600}a{color:#1f4e79}
.chip{border:1px solid var(--line);border-radius:99px;padding:1px 10px;font-size:12px;color:var(--mute);background:var(--bg)}
.chip.low{background:var(--low);border-color:var(--lowb);color:#6b4a00}.chip.ok{color:var(--ok);border-color:var(--ok)}
main{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px;padding:16px 24px}
@media(max-width:1100px){main{grid-template-columns:1fr}}
.panel{background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}
.tabs{display:flex;border-bottom:1px solid var(--line)}.tabs button{flex:0 0 auto;border:0;background:none;padding:9px 16px;cursor:pointer;font:inherit;color:var(--mute)}
.tabs button[aria-selected=true]{color:var(--ink);border-bottom:2px solid var(--ink);font-weight:600}
.view{height:calc(100vh - 150px);min-height:420px;overflow:auto}.view iframe,.view img{width:100%;border:0;display:block}
pre{margin:0;padding:12px;font:12px/1.35 Consolas,monospace;white-space:pre}
form{padding:0 0 12px}h2{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--mute);margin:14px 14px 6px}
table{border-collapse:collapse;width:100%}td,th{padding:5px 8px;border-top:1px solid var(--line);text-align:left;vertical-align:top}
th{font-size:12px;color:var(--mute);font-weight:500}td.k{width:34%;color:var(--mute)}
input[type=text]{width:100%;padding:5px 7px;border:1px solid var(--line);border-radius:5px;font:inherit;background:#fff}
tr.low input[type=text],td.low input[type=text]{background:var(--low);border-color:var(--lowb)}
table.lines td:nth-child(2){width:36%}table.lines td{padding:4px}
.why{font-size:12px;color:#6b4a00;margin-top:2px}.bar{padding:10px 14px;display:flex;gap:10px;align-items:center}
button.primary{background:var(--ink);color:#fff;border:0;border-radius:6px;padding:8px 16px;font:inherit;cursor:pointer}
.warn{background:var(--low);border:1px solid var(--lowb);padding:8px 14px;margin:12px 24px 0;border-radius:6px}
.err{background:#fde8e8;border:1px solid #c0392b;padding:8px 14px;margin:12px 24px 0;border-radius:6px}
.note{color:var(--mute);font-size:12px;padding:0 14px}
"""

REASON_TEXT = {
    "not_in_source": "not found in the document text",
    "date_not_near_label": "this date is not next to a due-date label",
    "label_present_value_null": "a due-date label with a date is on the page but the field is empty",
    "missing": "required value is missing",
}
RULE_TEXT = {
    "gstin_checksum": "GSTIN checksum is wrong",
    "gstin_format": "GSTIN format is wrong",
    "line_amount_mismatch": "quantity x rate does not equal the amount",
    "line_incomplete": "a row of the items table is incomplete",
    "supplier_is_buyer": "this is the buyer, not the seller",
    "tax_type_state_mismatch": "IGST or CGST+SGST does not fit the supplier state",
    "invoice_number_is_po": "looks like a purchase-order number",
    "subtotal_mismatch": "lines do not add up to the subtotal",
    "total_mismatch": "subtotal + GST does not equal the total",
    "tax_mismatch": "GST does not match rate x subtotal",
    "gst_rate_slab": "not a GST rate slab",
    "due_before_invoice": "due date is before the invoice date",
    "delivery_before_ship": "delivered before it was shipped",
    "duplicate_invoice_number": "same supplier and invoice number already stored",
    "duplicate_awb": "this AWB is already stored",
    "unparseable": "value could not be read",
    "missing": "required value is missing",
    "non_positive": "must be positive",
    "date_range": "implausible year",
    "awb_format": "unexpected AWB format",
    "currency_format": "not an ISO currency code",
}
WARNING_TEXT = {
    "instruction_in_document": "The document contains text that looks like an instruction to the extractor. It was ignored; check the values.",
    "document_type_uncertain": "The document type was a guess (no clear invoice or delivery-note wording).",
    "text_truncated": "The document text was cut to fit the model's context; late fields may be missing.",
}


def why(reasons: list[str]) -> str:
    out = []
    for r in reasons:
        if r.startswith("validation:"):
            out.append(RULE_TEXT.get(r.split(":", 1)[1], r))
        elif r.startswith("ocr_low_confidence"):
            out.append(f"low OCR confidence ({r.split(':', 1)[1]})")
        else:
            out.append(REASON_TEXT.get(r, r))
    return "; ".join(out)


def label(path: str) -> str:
    return path.replace("_", " ").replace("line items", "line")


def show(v: object) -> str:
    return "" if v is None else str(v)


def _input(path: str, value: object) -> str:
    return f'<input type="text" name="v::{escape(path)}" value="{escape(show(value), quote=True)}" autocomplete="off">'


def _confirm(path: str) -> str:
    return f'<label><input type="checkbox" name="ok::{escape(path)}"> confirm</label>'


def page(
    doc: dict,
    ext: dict,
    reviews: dict[str, dict],
    *,
    saved: bool = False,
    errors: list[str] | None = None,
    submitted: dict[str, str] | None = None,
) -> str:
    fields, doc_id = ext["fields"], doc["id"]
    low = [p for p, f in fields.items() if f["confidence"] == "low"]
    pending = [p for p in low if p not in reviews]
    kind = doc["kind"]
    if kind == "pdf":
        viewer = f'<iframe src="/files/{doc_id}" title="document" style="height:100%"></iframe>'
    else:
        viewer = f'<img src="/files/{doc_id}" alt="uploaded document">'
    chips = [
        f'<span class="chip">{escape(doc["doc_type"].replace("_", " "))}</span>',
        f'<span class="chip">{"scan, OCR" if doc["source"] == "scan" else "text PDF"}</span>',
        f'<span class="chip {"ok" if doc["status"] in ("reviewed", "auto_accepted") else "low"}">{escape(doc["status"])}</span>',
        f'<span class="chip {"low" if pending else "ok"}">{len(pending)} to review</span>',
    ]
    parts = [
        f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Review {doc_id}</title><style>{STYLE}</style>",
        f'<header><a href="/">All documents</a><h1>#{doc_id} {escape(doc["filename"])}</h1>{"".join(chips)}'
        f'<span class="note">simulated customer data (Larkfield Coffee Co.)</span></header>',
    ]
    if saved:
        parts.append('<div class="warn">Decisions saved.</div>')
    for e in errors or []:
        parts.append(f'<div class="err">{escape(e)}</div>')
    for w in ext["warnings"]:
        parts.append(f'<div class="warn">{escape(WARNING_TEXT.get(w, w))}</div>')
    parts.append(
        '<main><section class="panel"><div class="tabs" role="tablist">'
        '<button role="tab" aria-selected="true" data-t="v1">Document</button>'
        '<button role="tab" aria-selected="false" data-t="v2">Extracted text</button></div>'
        f'<div class="view" id="v1">{viewer}</div><div class="view" id="v2" hidden><pre>{escape(doc["text"] or "")}</pre></div>'
        "</section>"
    )
    parts.append(f'<section class="panel"><form method="post" action="/review/{doc_id}">')

    def row(path: str) -> str:
        f = fields[path]
        val = (submitted or {}).get(path, f["value"])
        rv = reviews.get(path)
        cls = ' class="low"' if f["confidence"] == "low" else ""
        reason = f'<div class="why">{escape(why(f["reasons"]))}</div>' if f["reasons"] else ""
        done = f'<div class="note">{escape(rv["decision"])}: {escape(show(rv["final_value"]))}</div>' if rv else ""
        conf = _confirm(path) if f["confidence"] == "low" else ""
        return f'<tr{cls}><td class="k">{escape(label(path))}{reason}</td><td>{_input(path, val)}{done}</td><td>{conf}</td></tr>'

    scalar_low = [p for p in low if p in FIELDS[doc["doc_type"]]]
    if scalar_low or "line_items" in low:
        parts.append("<h2>Needs review</h2><table>")
        parts += [row(p) for p in scalar_low]
        if "line_items" in low:
            f = fields["line_items"]
            parts.append(
                f'<tr class="low"><td class="k">line items<div class="why">{escape(why(f["reasons"]))}</div></td>'
                f"<td>check the line items below</td><td>{_confirm('line_items')}</td></tr>"
            )
        parts.append("</table>")
    if doc["doc_type"] == "invoice":
        n = len(ext["record"].get("line_items") or [])
        parts.append(
            "<h2>Line items</h2><table class=lines><tr><th>#</th>"
            + "".join(f"<th>{k}</th>" for k in LINE_FIELDS)
            + "<th></th></tr>"
        )
        for i in range(n):
            cells, any_low = [], False
            for name in LINE_FIELDS:
                p = line_path(i, name)
                f = fields[p]
                lowc = f["confidence"] == "low"
                any_low |= lowc
                val = (submitted or {}).get(p, f["value"])
                tip = f' title="{escape(why(f["reasons"]), quote=True)}"' if lowc else ""
                cls = ' class="low"' if lowc else ""
                note = f'<div class="why">{escape(why(f["reasons"]))}</div>' if lowc else ""
                cells.append(f"<td{cls}{tip}>{_input(p, val)}{note}</td>")
            parts.append(f"<tr><td>{i + 1}</td>{''.join(cells)}<td>{_line_confirm(i, any_low)}</td></tr>")
        parts.append("</table>")
    rest = [p for p in FIELDS[doc["doc_type"]] if p not in low]
    parts.append("<h2>Auto-accepted</h2><table>" + "".join(row(p) for p in rest) + "</table>")
    parts.append(
        '<div class="bar"><button class="primary" type="submit">Save decisions</button>'
        '<span class="note">Unchanged and confirmed = accepted. Edited = corrected. '
        "A document is reviewed when every low-confidence field has a decision.</span></div></form></section></main>"
    )
    parts.append(
        "<script>document.querySelectorAll('.tabs button').forEach(b=>b.addEventListener('click',()=>{"
        "document.querySelectorAll('.tabs button').forEach(x=>x.setAttribute('aria-selected',x===b));"
        "['v1','v2'].forEach(id=>document.getElementById(id).hidden=(id!==b.dataset.t));}));</script>"
    )
    return "".join(parts)


def _line_confirm(i: int, any_low: bool) -> str:
    if not any_low:
        return ""
    return f'<label><input type="checkbox" name="okline::{i}"> confirm line</label>'


def decide(doc: dict, ext: dict, form: dict[str, str], existing: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """Turn a submitted form into review rows. Returns (rows, errors). Nothing is saved when there are errors."""
    rows: list[dict] = []
    errors: list[str] = []
    fields = ext["fields"]
    for path, f in fields.items():
        if path == "line_items":
            if "ok::line_items" in form:
                rows.append(
                    {
                        "field": path,
                        "extracted": show(f["value"]),
                        "final": show(f["value"]),
                        "decision": "accepted",
                        "confidence": f["confidence"],
                        "reasons": json.dumps(f["reasons"]),
                    }
                )
            continue
        key = f"v::{path}"
        if key not in form:
            continue
        submitted = form[key].strip()
        original = show(f["value"])
        kind = kind_of(doc["doc_type"], path)
        if submitted == original:
            line_ok = path.startswith("line_items[") and f"okline::{path[len('line_items[') :].split(']')[0]}" in form
            if f["confidence"] == "low" and f"ok::{path}" not in form and not line_ok:
                continue  # still pending
            rows.append(
                {
                    "field": path,
                    "extracted": original,
                    "final": original,
                    "decision": "accepted",
                    "confidence": f["confidence"],
                    "reasons": json.dumps(f["reasons"]),
                }
            )
            continue
        value, ok = canonical_value(kind, submitted if submitted else None)
        if not ok:
            errors.append(f"{label(path)}: '{submitted}' is not a valid {kind}")
            continue
        rows.append(
            {
                "field": path,
                "extracted": original,
                "final": show(value),
                "decision": "corrected",
                "confidence": f["confidence"],
                "reasons": json.dumps(f["reasons"]),
            }
        )
    return rows, errors


def final_record(ext: dict, reviews: dict[str, dict], doc_type: str) -> dict:
    """The extraction with reviewer decisions applied (what goes to the spreadsheet)."""
    rec = json.loads(json.dumps(ext["record"]))
    for path, rv in reviews.items():
        if path == "line_items":
            continue
        kind = kind_of(doc_type, path)
        value, _ = canonical_value(kind, rv["final_value"] if rv["final_value"] != "" else None)
        if path.startswith("line_items["):
            idx, name = path[len("line_items[") :].split("].")
            rec["line_items"][int(idx)][name] = value
        else:
            rec[path] = value
    return rec


def safe_cell(v: object) -> object:
    """Stop spreadsheet formula injection from document text."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def export_csv(items: list[tuple[dict, dict, dict[str, dict]]]) -> str:
    """items: (document row, extraction, reviews). One row per document, final values."""
    scalars: list[str] = []
    for t in FIELDS.values():
        scalars += [f for f in t if f not in scalars]
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["document_id", "filename", "doc_type", "status", *scalars, "line_items_json", "corrected_fields"])
    for doc, ext, reviews in items:
        rec = final_record(ext, reviews, doc["doc_type"])
        corrected = [p for p, r in reviews.items() if r["decision"] == "corrected"]
        w.writerow(
            [
                doc["id"],
                safe_cell(doc["filename"]),
                doc["doc_type"],
                doc["status"],
                *[safe_cell(rec.get(f)) if f in FIELDS[doc["doc_type"]] else "" for f in scalars],
                json.dumps(rec.get("line_items", [])) if doc["doc_type"] == "invoice" else "",
                ";".join(corrected),
            ]
        )
    return out.getvalue()


def decisions_csv(rows: list[dict]) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(
        [
            "document_id",
            "filename",
            "field",
            "extracted_value",
            "final_value",
            "decision",
            "confidence",
            "reasons",
            "reviewed_at",
        ]
    )
    for r in rows:
        w.writerow(
            [
                r["document_id"],
                safe_cell(r["filename"]),
                r["field"],
                safe_cell(r["extracted_value"]),
                safe_cell(r["final_value"]),
                r["decision"],
                r["confidence"],
                r["reasons"],
                r["reviewed_at"],
            ]
        )
    return out.getvalue()
