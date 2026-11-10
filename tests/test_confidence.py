from app import confidence as c
from app.config import settings
from app.textlayer import Line, Page, TextDoc
from app.validate import Flag


def text_doc(text: str, segs: list[tuple[str, float]] | None = None) -> TextDoc:
    """A text-layer document, or an OCR'd one when segments with confidences are given."""
    if segs is None:
        return TextDoc("pdf", [Page(1, "pdf", text, [Line(t, None) for t in text.splitlines()])])
    return TextDoc("png", [Page(1, "ocr", text, [Line(t, cf, [(t, cf)]) for t, cf in segs])])


LAKH = text_doc("Total Amount  ₹ 1,23,456.50   IGST @ 18%   Date 04-Aug-2026   GSTIN: 29AABCK3381M1Z4")


def has(src, kind, value, field=""):
    return src.has(kind, value, field)


def test_numbers_match_after_normalisation():
    src = c.SourceIndex(LAKH)
    assert has(src, "amount", 123456.5)
    assert has(src, "amount", 123456.50)
    assert not has(src, "amount", 123456.6)
    assert has(src, "rate", 18.0)


def test_half_values_accepted_for_cgst_sgst_split():
    src = c.SourceIndex(text_doc("CGST @ 9%   1,500.00      SGST @ 9%   1,500.00"))
    assert has(src, "rate", 18.0, "gst_rate")
    assert has(src, "amount", 3000.0, "gst_amount")
    assert not has(src, "amount", 3000.0, "total")  # the half rule applies to GST fields only


def test_dates_gstin_and_ids():
    src = c.SourceIndex(LAKH)
    assert has(src, "date", "2026-08-04")
    assert not has(src, "date", "2026-04-08")
    assert has(src, "gstin", "29AABCK3381M1Z4")
    assert not has(src, "gstin", "29AABCK3381M1Z5")


def test_names_match_across_spacing_and_wrapped_rows():
    src = c.SourceIndex(text_doc("Kaveri Ridge Estates\nPvt Ltd   Hyderabad"))
    assert has(src, "name", "Kaveri Ridge Estates Pvt Ltd")
    assert has(src, "name", "kaveri ridge estates")
    assert not has(src, "name", "Kaveri Hills Estates")


def test_currency_inr_accepts_rupee_markers_or_gst_wording():
    assert has(c.SourceIndex(text_doc("Amount Rs. 100")), "currency", "INR")
    assert has(c.SourceIndex(text_doc("Total ₹ 100")), "currency", "INR")
    assert has(c.SourceIndex(text_doc("GSTIN 29AABCK3381M1Z4")), "currency", "INR")
    assert not has(c.SourceIndex(text_doc("Total 100 dollars")), "currency", "INR")
    assert has(c.SourceIndex(text_doc("Price USD 100")), "currency", "USD")


def record():
    return {
        "supplier_name": "Kaveri Ridge Estates Pvt Ltd",
        "supplier_gstin": "29AABCK3381M1Z4",
        "invoice_number": None,
        "invoice_date": "2026-08-04",
        "due_date": None,
        "subtotal": 104657.2,
        "gst_rate": 18.0,
        "gst_amount": 18798.78,
        "total": 123456.5,
        "currency": "INR",
        "line_items": [{"description": "Widget", "qty": 2.0, "unit_price": 5.0, "amount": 10.0}],
    }


SOURCE = (
    "Kaveri Ridge Estates Pvt Ltd  GSTIN 29AABCK3381M1Z4  Date 04-Aug-2026  Subtotal 1,04,657.20  IGST 18%  "
    "18,798.78  Total 1,23,456.50  Rs\nWidget   2   5.00   10.00"
)


def test_assess_all_high_when_verbatim_and_unflagged():
    out = c.assess("invoice", record(), [], text_doc(SOURCE))
    assert {p for p, f in out.items() if f["confidence"] == "low"} == set()
    assert out["total"]["value"] == 123456.5 and out["total"]["reasons"] == []


def test_validation_flag_makes_field_low():
    flags = [Flag("total", "total_mismatch", "x")]
    out = c.assess("invoice", record(), flags, text_doc(SOURCE))
    assert out["total"]["confidence"] == "low" and out["total"]["reasons"] == ["validation:total_mismatch"]


def test_value_not_in_source_is_low():
    rec = record()
    rec["total"] = 123499.5
    out = c.assess("invoice", rec, [], text_doc(SOURCE))
    assert out["total"]["reasons"] == ["not_in_source"]


def test_null_optional_fields_are_not_low():
    out = c.assess("invoice", record(), [], text_doc(SOURCE))
    assert out["invoice_number"]["confidence"] == "high" and out["due_date"]["confidence"] == "high"


def test_missing_required_comes_from_the_validation_flag():
    out = c.assess("invoice", record(), [Flag("invoice_number", "missing", "x")], text_doc(SOURCE))
    assert out["invoice_number"]["confidence"] == "low"


def test_ocr_low_confidence_marks_only_the_matching_segment():
    segs = [
        ("Kaveri Ridge Estates Pvt Ltd", 0.95),
        ("Total 1,23,456.50", settings.ocr_low_conf - 0.05),
        ("Date 04-Aug-2026", 0.95),
        ("GSTIN 29AABCK3381M1Z4", 0.95),
        ("Subtotal 1,04,657.20", 0.9),
        ("IGST 18% 18,798.78 Rs Widget 2 5.00 10.00", 0.9),
    ]
    out = c.assess("invoice", record(), [], text_doc(SOURCE, segs))
    assert out["total"]["confidence"] == "low" and out["total"]["reasons"][0].startswith("ocr_low_confidence")
    assert out["invoice_date"]["confidence"] == "high"


def test_ocr_confidence_ignored_for_text_layer_documents():
    out = c.assess("invoice", record(), [], text_doc(SOURCE))
    assert not any(r.startswith("ocr") for f in out.values() for r in f["reasons"])


def test_best_segment_counts_when_a_number_appears_twice():
    segs = [("Total 1,23,456.50", 0.40), ("Amount payable 1,23,456.50", 0.95)]
    src = c.SourceIndex(text_doc("x", segs))
    assert src.ocr_conf("amount", 123456.5) == 0.95


def test_due_date_label_with_a_date_but_empty_field_is_flagged():
    src = SOURCE + "\nDue Date   15-Aug-2026"
    out = c.assess("invoice", record(), [], text_doc(src))
    assert out["due_date"]["reasons"] == ["label_present_value_null"]


def test_blank_due_date_label_is_not_flagged():
    src = SOURCE + "\nDue Date   Bill To\nLarkfield"
    out = c.assess("invoice", record(), [], text_doc(src))
    assert out["due_date"]["confidence"] == "high"


def test_list_level_flag_creates_line_items_entry():
    out = c.assess("invoice", record(), [Flag("line_items", "subtotal_mismatch", "x")], text_doc(SOURCE))
    assert out["line_items"]["confidence"] == "low" and out["line_items"]["value"] == 1


def test_line_cells_have_paths():
    out = c.assess("invoice", record(), [], text_doc(SOURCE))
    assert {
        "line_items[0].description",
        "line_items[0].qty",
        "line_items[0].unit_price",
        "line_items[0].amount",
    } <= set(out)
