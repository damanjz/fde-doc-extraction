import json

from app import review
from tests.conftest import run_doc


def ext_doc(db, mock, doc_id):
    d, _ = run_doc(doc_id, db, mock)
    return db.get_document(d), db.get_extraction(d)


def form_unchanged(ext, confirm_low=True):
    form = {}
    for path, f in ext["fields"].items():
        if path == "line_items":
            if confirm_low:
                form["ok::line_items"] = "on"
            continue
        form[f"v::{path}"] = "" if f["value"] is None else str(f["value"])
        if confirm_low and f["confidence"] == "low":
            form[f"ok::{path}"] = "on"
    return form


def test_unchanged_confirmed_fields_are_accepted(db, mock):
    doc, ext = ext_doc(db, mock, "inv_005")
    rows, errors = review.decide(doc, ext, form_unchanged(ext), {})
    assert errors == [] and {r["decision"] for r in rows} == {"accepted"}
    assert len(rows) == len([p for p in ext["fields"] if not p.startswith("line_items")]) + len(
        [p for p in ext["fields"] if p.startswith("line_items[")]
    )


def test_low_field_without_confirmation_stays_pending(db, mock):
    doc, ext = ext_doc(db, mock, "inv_005")
    rows, _ = review.decide(doc, ext, form_unchanged(ext, confirm_low=False), {})
    assert "supplier_gstin" not in {r["field"] for r in rows}
    assert "invoice_number" in {r["field"] for r in rows}


def test_edit_is_a_correction_with_a_canonical_value(db, mock):
    doc, ext = ext_doc(db, mock, "inv_017")
    form = form_unchanged(ext)
    form["v::total"] = "₹ 1,23,456.50"
    rows, errors = review.decide(doc, ext, form, {})
    row = next(r for r in rows if r["field"] == "total")
    assert errors == [] and row["decision"] == "corrected" and row["final"] == "123456.5"
    assert row["extracted"] == str(ext["fields"]["total"]["value"])


def test_invalid_edit_is_an_error_and_not_a_row(db, mock):
    doc, ext = ext_doc(db, mock, "inv_017")
    form = form_unchanged(ext)
    form["v::invoice_date"] = "someday"
    rows, errors = review.decide(doc, ext, form, {})
    assert len(errors) == 1 and "invoice date" in errors[0]
    assert "invoice_date" not in {r["field"] for r in rows}


def test_clearing_a_value_corrects_it_to_empty(db, mock):
    doc, ext = ext_doc(db, mock, "inv_003")
    form = form_unchanged(ext)
    form["v::due_date"] = ""
    form["v::supplier_name"] = ""
    rows, _ = review.decide(doc, ext, form, {})
    row = next(r for r in rows if r["field"] == "supplier_name")
    assert row["decision"] == "corrected" and row["final"] == ""


def test_final_record_applies_reviews(db, mock):
    doc, ext = ext_doc(db, mock, "inv_008")
    reviews = {
        "line_items[2].amount": {"final_value": "5000.0"},
        "supplier_name": {"final_value": "New Name"},
        "due_date": {"final_value": ""},
        "line_items": {"final_value": "3"},
    }
    rec = review.final_record(ext, reviews, "invoice")
    assert rec["line_items"][2]["amount"] == 5000.0 and rec["supplier_name"] == "New Name" and rec["due_date"] is None


def test_safe_cell_blocks_formulas():
    for bad in ("=SUM(A1)", "+1", "-1+2", "@x", "\tx"):
        assert review.safe_cell(bad).startswith("'")
    assert review.safe_cell("ok") == "ok" and review.safe_cell(12.5) == 12.5 and review.safe_cell(None) is None


def test_page_escapes_hostile_values(db, mock):
    doc, ext = ext_doc(db, mock, "inv_003")
    ext["fields"]["supplier_name"]["value"] = '"><script>alert(1)</script>'
    doc["text"] = "<script>alert(2)</script>"
    html = review.page(doc, ext, {})
    assert "<script>alert" not in html


def test_why_text_is_readable():
    assert review.why(["validation:gstin_checksum", "not_in_source", "ocr_low_confidence:0.62"]) == (
        "GSTIN checksum is wrong; not found in the document text; low OCR confidence (0.62)"
    )


def test_csv_header_covers_both_document_types(db, mock):
    items = []
    for doc_id in ("inv_003", "dn_001"):
        doc, ext = ext_doc(db, mock, doc_id)
        items.append((doc, ext, {}))
    out = review.export_csv(items).splitlines()
    header = out[0].split(",")
    assert "supplier_gstin" in header and "awb_number" in header and "line_items_json" in header
    assert len(out) == 3
    assert json.loads(next(iter(__import__("csv").DictReader([out[0], out[1]])))["line_items_json"])
