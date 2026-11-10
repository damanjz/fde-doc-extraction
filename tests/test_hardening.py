"""Review round 2026-11-10: wrong extractions run through the real pipeline and scorer, hostile model output,
hostile uploads, the Origin check, document statuses and the scorer's rules."""

import copy
import io
import json
import time
import zlib
from functools import cache

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main, pipeline, textlayer
from app import validate as v
from app.config import settings
from app.extract import ExtractionError
from app.llm import LLMResponse
from app.normalise import canonical_value, parse_decimal
from app.textlayer import read_document
from evals import run as ev
from evals import scoring as sc
from tests.conftest import TRUTH, doc_bytes
from tests.test_textlayer import make_pdf

BUYER_GSTIN = settings.buyer_gstin
BUYER_NAME = settings.buyer_name


@cache
def _td(doc_id: str):
    return read_document(doc_bytes(doc_id))


def attack(doc_id, mutate=None, prior=(), raw=None):
    """Run a wrong (or hostile) model reply through the real pipeline and the real scorer."""
    t = TRUTH[doc_id]
    f = copy.deepcopy(t["fields"])
    if mutate:
        mutate(f)
    content = raw if raw is not None else json.dumps(f)
    res = pipeline.run(_td(doc_id), lambda dt: list(prior), lambda m: LLMResponse(content=content, model="fake"))
    out = {
        "record": res.record,
        "low": {p: x["reasons"] for p, x in res.fields.items() if x["confidence"] == "low"},
        "warnings": res.warnings,
        "doc_type": res.doc_type,
        "timings": res.timings,
    }
    return res, sc.score_doc(t, out)


def setp(path, val):
    def m(f):
        if path.startswith("line_items["):
            i, n = path[len("line_items[") :].split("].")
            f["line_items"][int(i)][n] = val
        else:
            f[path] = val

    return m


def reasons(res, path):
    return res.fields[path]["reasons"]


def silent(d):
    return [c for c in d.pipeline if not c.correct and not c.flagged]


# --- the buyer is never the seller ---------------------------------------------------------------


def test_control_perfect_extraction_is_auto_accepted():
    res, d = attack("inv_003")
    assert res.low_fields == [] and res.status == "auto_accepted" and not silent(d)


def test_supplier_gstin_equal_to_the_buyers_is_flagged():
    res, d = attack("inv_003", setp("supplier_gstin", BUYER_GSTIN))
    assert "validation:supplier_is_buyer" in reasons(res, "supplier_gstin") and not silent(d)


def test_supplier_name_matching_the_buyer_is_flagged():
    for name in (BUYER_NAME, "LARKFIELD COFFEE CO. PVT LTD", "Larkfield Coffee"):
        res, d = attack("inv_003", setp("supplier_name", name))
        assert "validation:supplier_is_buyer" in reasons(res, "supplier_name"), name
        assert not silent(d)


def test_buyer_rule_does_not_fire_on_real_suppliers():
    assert v.check_supplier_not_buyer(TRUTH["inv_003"]["fields"]) == []
    assert v.check_supplier_not_buyer({"supplier_name": "Lark"}) == []  # too short to be a name match


# --- tax type must agree with the supplier's state -------------------------------------------------


def test_tax_type_text_detection():
    assert v.tax_type_in_text("IGST @ 18%") == "igst"
    assert v.tax_type_in_text("CGST @ 9%  SGST @ 9%") == "cgst_sgst"
    assert v.tax_type_in_text("GST 18%") is None
    assert v.tax_type_in_text("IGST CGST SGST columns") is None


def rec_with_state(state: str) -> dict:
    from app import gstin

    return {"supplier_gstin": gstin.make(state, "AAHFB2279K")}


def test_same_state_as_buyer_must_charge_cgst_sgst():
    flags = v.check_tax_type(rec_with_state("36"), "Taxable 100  IGST @ 18%  18")
    assert {(f.field, f.rule) for f in flags} == {
        ("supplier_gstin", "tax_type_state_mismatch"),
        ("gst_amount", "tax_type_state_mismatch"),
    }
    assert v.check_tax_type(rec_with_state("36"), "CGST @ 9%  SGST @ 9%") == []


def test_other_state_must_charge_igst():
    assert v.check_tax_type(rec_with_state("29"), "CGST @ 9%  SGST @ 9%") != []
    assert v.check_tax_type(rec_with_state("29"), "IGST @ 18%") == []


def test_tax_type_check_is_silent_when_nothing_is_stated_or_the_gstin_is_unreadable():
    assert v.check_tax_type(rec_with_state("29"), "GST 18%") == []
    assert v.check_tax_type({"supplier_gstin": "garbled"}, "IGST @ 18%") == []
    assert v.check_tax_type({"supplier_gstin": None}, "IGST @ 18%") == []


def test_a_wrong_state_code_on_a_real_invoice_is_flagged_through_the_pipeline():
    # inv_003 is a Delhi supplier charging IGST; a model that reads the GSTIN as a Telangana one is inconsistent
    res, _ = attack("inv_003", setp("supplier_gstin", "36AAHFB2279K1Z1"))
    assert "validation:tax_type_state_mismatch" in reasons(res, "supplier_gstin")


def test_every_unplanted_generated_invoice_is_consistent_on_tax_type():
    for doc_id, t in TRUTH.items():
        if t["type"] == "invoice" and t["source"] == "pdf":
            assert v.check_tax_type(t["fields"], _td(doc_id).text) == [], doc_id


# --- not in the source: as strict as the scorer --------------------------------------------------


@pytest.mark.parametrize(
    "value", ["BBW-389-26-27", "389/26-27", "BBW/389/26", "bbw/389/26-27", "BBW/389/26-27/", "BBW 389 26 27"]
)
def test_invoice_number_must_match_punctuation_and_not_be_a_piece(value):
    res, d = attack("inv_003", setp("invoice_number", value))
    assert "not_in_source" in reasons(res, "invoice_number"), value
    assert not silent(d)


def test_invoice_number_spaces_are_ignored_but_the_number_passes_untouched():
    res, _ = attack("inv_003", setp("invoice_number", "BBW/389/26-27"))
    assert res.fields["invoice_number"]["confidence"] == "high"


@pytest.mark.parametrize(
    "name", ["Pearl City Kitchenware Pvt. Ltd.", "Pearl City", "Pearl City Kitchenware", "City Kitchenware Pvt Ltd"]
)
def test_names_keep_punctuation_and_truncations_do_not_pass(name):
    res, d = attack("inv_019", setp("supplier_name", name))
    assert "not_in_source" in reasons(res, "supplier_name"), name
    assert not silent(d)


def test_name_passes_when_case_and_spacing_differ_only():
    res, _ = attack("inv_019", setp("supplier_name", "pearl city  KITCHENWARE pvt ltd"))
    assert res.fields["supplier_name"]["confidence"] == "high"


def test_wrapped_description_must_be_complete():
    full = TRUTH["inv_005"]["fields"]["line_items"][0]["description"]
    res, _ = attack("inv_005", setp("line_items[0].description", full))
    assert res.fields["line_items[0].description"]["confidence"] == "high"  # a two-row wrap is found whole
    res, d = attack("inv_005", setp("line_items[0].description", "Chicory powder 25 kg sack - Lot"))
    assert "not_in_source" in reasons(res, "line_items[0].description") and not silent(d)


def test_wrap_split_into_an_extra_line_is_flagged_everywhere_it_matters():
    def split(f):
        f["line_items"][0]["description"] = "Chicory powder 25 kg sack - Lot"
        f["line_items"].insert(1, {"description": "BRB/505-A", "qty": None, "unit_price": None, "amount": None})

    res, d = attack("inv_005", split)
    assert not silent(d)
    assert any(c.extra for c in d.pipeline)


def test_gstin_must_be_a_whole_token():
    text = "GSTIN: 29AABCK3381M1Z4  PAN"
    from app import confidence as c
    from app.textlayer import Line, Page, TextDoc

    src = c.SourceIndex(TextDoc("pdf", [Page(1, "pdf", text, [Line(text, None)])]))
    assert src.has("gstin", "29AABCK3381M1Z4")
    assert src.has("gstin", "29aabck3381m1z4")
    assert not src.has("gstin", "29AABCK3381M1Z")  # truncated
    assert not src.has("gstin", "9AABCK3381M1Z4")


# --- due date next to a due label; invoice number is not a PO number -------------------------------


def test_due_date_copied_from_the_invoice_date_is_flagged():
    res, d = attack("inv_003", setp("due_date", "2026-10-10"))  # the invoice date, in the text, not by a due label
    assert reasons(res, "due_date") == ["date_not_near_label"] and not silent(d)


def test_invented_due_date_is_flagged_even_when_the_date_is_in_the_text():
    res, d = attack("inv_005", setp("due_date", "2026-08-25"))  # the issue date; the truth has no due date
    assert "due_date" in res.low_fields and not silent(d)


def test_due_date_next_to_its_label_is_fine():
    for doc_id in ("inv_002", "inv_003", "inv_004", "inv_010", "inv_026"):  # layouts 2, 3, 4, 5, 1
        res, _ = attack(doc_id)
        assert res.fields["due_date"]["confidence"] == "high", doc_id


def test_overdue_in_the_notes_is_not_a_due_label():
    from app import confidence as c
    from app.textlayer import Line, Page, TextDoc

    text = "Interest on overdue payments 12-08-2026"
    src = c.SourceIndex(TextDoc("pdf", [Page(1, "pdf", text, [Line(text, None)])]))
    assert src.due_dates_near_label() == set()


def test_invoice_number_that_is_a_po_number_is_flagged():
    res, d = attack("inv_003", setp("invoice_number", "LCC/PO/26-27/259"))
    assert "validation:invoice_number_is_po" in reasons(res, "invoice_number") and not silent(d)
    assert v.check_invoice_number_not_po({"invoice_number": "BBW/389/26-27"}) == []


# --- line alignment and the list flag ----------------------------------------------------------------


def test_a_dropped_middle_line_leaves_nothing_silent():
    def drop(f):
        f["line_items"].pop(1)

    res, d = attack("inv_003", drop)
    assert "line_items" in res.low_fields and not silent(d)
    assert all(c.flagged for c in d.pipeline if c.name.startswith("line_items."))  # whole list flagged


def test_a_dropped_last_line_leaves_nothing_silent():
    def drop(f):
        f["line_items"].pop()

    _, d = attack("inv_003", drop)
    assert not silent(d)


def test_swapped_lines_are_wrong_by_position_and_right_by_content():
    def swap(f):
        li = f["line_items"]
        li[0], li[1] = li[1], li[0]

    _, d = attack("inv_003", swap)
    wrong = [c for c in d.pipeline if not c.correct]
    assert len(wrong) == 8 and len(silent(d)) == 8  # sums unchanged: silent
    assert all(d.line_content)  # content alignment sees correct data in a different order
    la = sc.line_alignment([d])
    assert la["pos_n"] == la["con_n"] == 16 and la["pos_ok"] == 8 and la["con_ok"] == 16


def test_qty_rate_swap_is_a_known_undetected_error():
    def swap(f):
        ln = f["line_items"][0]
        ln["qty"], ln["unit_price"] = ln["unit_price"], ln["qty"]

    res, d = attack("inv_003", swap)
    assert res.low_fields == [] and len(silent(d)) == 2  # documented limit, see README


def test_numbers_that_are_wrong_but_in_the_text_are_caught_by_the_sums():
    res, d = attack("inv_019", setp("line_items[0].amount", 88436.5))  # line 1's amount
    assert "validation:line_amount_mismatch" in reasons(res, "line_items[0].amount") and not silent(d)
    res, d = attack("inv_019", setp("gst_amount", 35067.44))  # CGST only
    assert "validation:tax_mismatch" in reasons(res, "gst_amount") or "validation:total_mismatch" in reasons(
        res, "gst_amount"
    )
    res, d = attack("inv_019", setp("total", 0))
    assert {"validation:total_mismatch", "validation:non_positive"} <= set(reasons(res, "total"))


def test_half_the_gst_rate_alone_is_flagged():
    res, _ = attack("inv_019", setp("gst_rate", 9.0))
    assert "validation:tax_mismatch" in reasons(res, "gst_rate")


def test_dates_in_the_wrong_order_of_day_and_month_are_flagged():
    res, _ = attack("inv_019", setp("invoice_date", "09/29/2026"))  # US format: not a date at all
    assert reasons(res, "invoice_date")
    res, _ = attack("inv_003", setp("due_date", "2026-09-12"))  # printed 09.12.2026 = 9 December
    assert "not_in_source" in reasons(res, "due_date")


def test_currency_rules_through_the_pipeline():
    res, _ = attack("inv_019", setp("currency", "USD"))
    assert "not_in_source" in reasons(res, "currency")
    res, _ = attack("inv_019", setp("currency", None))
    assert "validation:missing" in reasons(res, "currency")


# --- hostile model output ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,path",
    [
        ('{"total": NaN, "subtotal": 1}', "total"),
        ('{"total": Infinity}', "total"),
        ('{"total": -Infinity}', "total"),
        ('{"total": 1e400}', "total"),
        ('{"line_items":[{"qty": -Infinity, "unit_price": 1, "amount": 1, "description": "x"}]}', "line_items[0].qty"),
        ('{"gst_rate": "18%"}', "gst_rate"),
        ('{"total": "\\u0967\\u0968\\u0969"}', "total"),
        ('{"invoice_date": "2026-02-30"}', "invoice_date"),
        ('{"total": 1e30}', "total"),
        ('{"total": "1e5"}', "total"),
    ],
)
def test_non_finite_and_malformed_numbers_never_crash_and_are_flagged(raw, path):
    res, _ = attack("inv_019", raw=raw)
    assert res.fields[path]["confidence"] == "low"


@pytest.mark.parametrize(
    "raw",
    [
        '{"line_items": {"a": 1}}',
        '{"line_items": ["a", "b"]}',
        '{"invoice": [1, 2]}',
        '{"invoice_date": "9999-12-31", "due_date": "0001-01-01"}',
        json.dumps({"supplier_name": "A" * 200000}),
        '{"supplier_name": {"x": 1}, "total": [1], "line_items": null}',
        "{}",
    ],
)
def test_odd_shapes_are_recorded_as_flagged_not_crashes(raw):
    res, _ = attack("inv_019", raw=raw)
    assert res.low_fields
    assert len(str(res.record["supplier_name"] or "")) <= 1100


def test_non_finite_delivery_note_numbers():
    td = _td("dn_001")
    res = pipeline.run(td, lambda d: [], lambda m: LLMResponse(content='{"packages": Infinity, "weight_kg": NaN}'))
    assert res.fields["packages"]["confidence"] == "low" and res.fields["weight_kg"]["confidence"] == "low"


def test_parse_decimal_rejects_everything_non_finite_or_absurd():
    for bad in (float("nan"), float("inf"), float("-inf"), "NaN", "Infinity", "1e5", 10**14, "१२"):
        assert parse_decimal(bad) is None, bad
    assert parse_decimal(12345678.5) is not None
    assert canonical_value("int", float("inf")) == ("inf", False) or canonical_value("int", float("inf"))[1] is False


def test_unexpected_exception_after_the_model_answers_becomes_a_clean_failure(monkeypatch):
    from app import confidence

    def boom(*a, **k):
        raise ZeroDivisionError("bad")

    monkeypatch.setattr(confidence, "assess", boom)
    with pytest.raises(ExtractionError) as e:
        pipeline.run(
            _td("inv_003"), lambda d: [], lambda m: LLMResponse(content=json.dumps(TRUTH["inv_003"]["fields"]))
        )
    assert "unusable model output" in str(e.value)


@pytest.fixture()
def client(db, mock):
    main.app.dependency_overrides[main.get_db] = lambda: db
    main.app.dependency_overrides[main.get_chat] = lambda: mock
    yield TestClient(main.app, follow_redirects=False)
    main.app.dependency_overrides.clear()


def test_api_returns_422_not_500_for_unusable_model_output(client, db, monkeypatch):
    from app import confidence

    def boom(*a, **k):
        raise RecursionError("deep")

    monkeypatch.setattr(confidence, "assess", boom)
    main.app.dependency_overrides[main.get_chat] = lambda: lambda m: LLMResponse(content="{}")
    r = client.post("/extract", files={"file": ("a.pdf", doc_bytes("inv_003"), "application/pdf")})
    assert r.status_code == 422 and "unusable model output" in r.json()["detail"]
    assert db.list_documents()[0]["status"] == "error"


def test_api_returns_422_when_the_model_never_returns_json(client):
    main.app.dependency_overrides[main.get_chat] = lambda: lambda m: LLMResponse(content="sorry, no")
    r = client.post("/extract", files={"file": ("a.pdf", doc_bytes("inv_003"), "application/pdf")})
    assert r.status_code == 422


def test_api_survives_nan_from_the_model(client):
    main.app.dependency_overrides[main.get_chat] = lambda: lambda m: LLMResponse(content='{"total": NaN}')
    r = client.post("/extract", files={"file": ("a.pdf", doc_bytes("inv_003"), "application/pdf")})
    assert r.status_code == 200 and "total" in r.json()["low_confidence"]


# --- hostile uploads -----------------------------------------------------------------------------------


def pdf_image(w, h, n=1):
    raw = zlib.compress(b"\xff" * (w * h), 9)
    pdf, offs = [b"%PDF-1.4\n"], []

    def add(s):
        offs.append(sum(len(x) for x in pdf))
        pdf.append(s)

    add(b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n")
    add(b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n")
    xo = b"".join(b"/Im%d %d 0 R" % (i, 5 + i) for i in range(n))
    add(
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 600 800]/Resources<</XObject<<"
        + xo
        + b">>>>/Contents 4 0 R>>endobj\n"
    )
    content = b"".join(b"q 100 0 0 100 0 0 cm /Im%d Do Q " % i for i in range(n))
    add(b"4 0 obj<</Length %d>>stream\n" % len(content) + content + b"\nendstream endobj\n")
    for i in range(n):
        add(
            b"%d 0 obj<</Type/XObject/Subtype/Image/Width %d/Height %d/ColorSpace/DeviceGray/BitsPerComponent 8"
            b"/Filter/FlateDecode/Length %d>>stream\n" % (5 + i, w, h, len(raw)) + raw + b"\nendstream endobj\n"
        )
    xref = sum(len(x) for x in pdf)
    pdf.append(b"xref\n0 %d\n0000000000 65535 f \n" % (len(offs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs))
    pdf.append(b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(offs) + 1, xref))
    return b"".join(pdf)


def test_pdf_image_dimensions_are_read_before_decoding(monkeypatch):
    """A 7000x7000 image (49 megapixels) is refused from its dictionary: nothing is decoded, no OCR runs."""
    from pypdf._page import PageObject

    def no_decode(*a, **k):
        raise AssertionError("an image was decoded before the size check")

    monkeypatch.setattr(PageObject, "_get_image", no_decode)
    monkeypatch.setattr(textlayer.ocr, "ocr_image", no_decode)
    t0 = time.monotonic()
    with pytest.raises(textlayer.UploadError) as e:
        read_document(pdf_image(7000, 7000))
    assert e.value.status == 422 and "too large" in e.value.message and time.monotonic() - t0 < 3


def test_too_many_images_on_one_page_are_refused(monkeypatch):
    with pytest.raises(textlayer.UploadError) as e:
        read_document(pdf_image(10, 10, textlayer.MAX_IMAGES_PER_PAGE + 1))
    assert e.value.status == 422 and "images on one page" in e.value.message


def test_a_small_scanned_pdf_with_a_few_images_is_still_read():
    td = read_document(pdf_image(120, 80, 2))
    assert td.pages[0].source == "ocr"


def test_png_over_the_pixel_limit_is_refused_after_open_and_before_load(monkeypatch):
    buf = io.BytesIO()
    Image.new("L", (8000, 8000), 255).save(buf, "PNG")
    loaded = []
    orig = Image.Image.load
    monkeypatch.setattr(Image.Image, "load", lambda self: loaded.append(1) or orig(self))
    with pytest.raises(textlayer.UploadError) as e:
        read_document(buf.getvalue())
    assert e.value.status == 422 and "too large" in e.value.message and loaded == []


def test_api_refuses_a_decompression_bomb_pdf(client):
    r = client.post("/extract", files={"file": ("b.pdf", pdf_image(7000, 7000, 4), "application/pdf")})
    assert r.status_code == 422


# --- Origin check ---------------------------------------------------------------------------------------


def test_cross_site_posts_are_refused(client, mock):
    mock.current = "inv_005"
    ok = client.post("/extract", files={"file": ("a.pdf", doc_bytes("inv_005"), "application/pdf")})
    assert ok.status_code == 200
    url = ok.json()["review_url"]
    form = {"v::invoice_number": "x"}
    for headers in ({"origin": "http://evil.example"}, {"origin": "null"}, {"referer": "http://evil.example/x"}):
        assert client.post(url, data=form, headers=headers).status_code == 403, headers
        up = client.post(
            "/extract", files={"file": ("a.pdf", doc_bytes("inv_005"), "application/pdf")}, headers=headers
        )
        assert up.status_code == 403
    assert client.post(url, data=form, headers={"origin": "http://testserver"}).status_code in (303, 422)
    assert client.post(url, data=form).status_code in (303, 422)  # no Origin (curl): allowed


# --- document status and export ---------------------------------------------------------------------------


def test_clean_document_is_auto_accepted_and_exported_by_default(client, mock):
    mock.current = "inv_003"
    j = client.post("/extract", files={"file": ("a.pdf", doc_bytes("inv_003"), "application/pdf")}).json()
    assert j["low_confidence"] == [] and j["warnings"] == []
    assert client.get(f"/review/{j['id']}").status_code == 200
    rows = client.get("/export.csv").text.splitlines()
    assert len(rows) == 2 and "auto_accepted" in rows[1] and TRUTH["inv_003"]["fields"]["invoice_number"] in rows[1]


def test_flagged_document_needs_a_person_and_is_not_exported_by_default(client, mock):
    mock.current = "inv_005"
    client.post("/extract", files={"file": ("a.pdf", doc_bytes("inv_005"), "application/pdf")})
    assert len(client.get("/export.csv").text.splitlines()) == 1
    assert len(client.get("/export.csv?all=1").text.splitlines()) == 2


def test_uncertain_document_type_counts_as_a_human_touch(db):
    def chat(m):
        return LLMResponse(content='{"supplier_name": "Hello Co"}')

    pdf = make_pdf(1, "hello world this is just some words and nothing else about anything here")
    doc_id, res = pipeline.ingest(db, pdf, "x.pdf", chat, keep_file=False)
    assert "document_type_uncertain" in res.warnings and res.status == "extracted"
    assert db.get_document(doc_id)["status"] == "extracted"


def test_saving_a_review_marks_an_auto_accepted_document_reviewed(client, mock, db):
    mock.current = "inv_003"
    j = client.post("/extract", files={"file": ("a.pdf", doc_bytes("inv_003"), "application/pdf")}).json()
    form = {
        f"v::{p}": ("" if f["value"] is None else str(f["value"])) for p, f in j["fields"].items() if p != "line_items"
    }
    assert client.post(j["review_url"], data=form).status_code == 303
    assert db.get_document(j["id"])["status"] == "reviewed"


# --- the scorer ----------------------------------------------------------------------------------------------


def test_numeric_field_holding_a_string_is_wrong():
    assert not sc.equal("amount", 100.0, "100")
    assert not sc.equal("amount", 100.0, "1e2")
    assert not sc.equal("qty", 2.0, "2")
    assert not sc.equal("int", 7, "7")
    assert not sc.equal("amount", 100.0, True)
    assert not sc.equal("amount", "100", 100.0)
    assert sc.equal("amount", 100.0, 100) and sc.equal("int", 7, 7.0)
    assert not sc.equal("date", "2026-08-04", 20260804)


def test_denominator_is_the_truth_cells_and_extra_lines_are_errors_outside_it():
    t = TRUTH["inv_003"]
    f = copy.deepcopy(t["fields"])
    f["line_items"].append({"description": "Extra", "qty": 1.0, "unit_price": 1.0, "amount": 1.0})
    res = {"record": f, "low": {}, "warnings": [], "doc_type": "invoice", "timings": {}}
    d = sc.score_doc(t, res)
    s = sc.variant_stats([d], "pipeline")
    truth_cells = 10 + 4 * len(t["fields"]["line_items"])
    assert s["cells"] == truth_cells and s["correct"] == truth_cells
    assert s["wrong"] == 4 and s["silent"] == 4  # the extra line is four silent errors, outside the denominator
    assert s["bad_stp"] == 1


def test_denominator_is_fixed_when_lines_are_missing_or_the_document_failed():
    t = TRUTH["inv_003"]
    n = 10 + 4 * len(t["fields"]["line_items"])
    short = copy.deepcopy(t["fields"])
    short["line_items"].pop()
    assert (
        sc.variant_stats([sc.score_doc(t, {"record": short, "low": {}, "doc_type": "invoice"})], "pipeline")["cells"]
        == n
    )
    failed = sc.score_doc(t, {"record": None, "low": {}, "doc_type": None})
    s = sc.variant_stats([failed], "pipeline")
    assert s["cells"] == n and s["wrong"] == n and s["silent"] == 0 and s["touched"] == 1


def test_content_alignment_handles_dropped_and_extra_lines():
    t = {
        "type": "invoice",
        "fields": {
            "line_items": [
                {"description": "A", "qty": 1.0, "unit_price": 10.0, "amount": 10.0},
                {"description": "B", "qty": 2.0, "unit_price": 20.0, "amount": 40.0},
                {"description": "C", "qty": 3.0, "unit_price": 30.0, "amount": 90.0},
            ]
        },
    }
    p = copy.deepcopy(t["fields"])
    p["line_items"].pop(1)  # B dropped: position alignment shifts C, content alignment does not
    assert sc.align_lines(t["fields"]["line_items"], p["line_items"]) == [0, None, 1]
    ok = sc.line_cells_content(t, p)
    assert ok == [True] * 4 + [False] * 4 + [True] * 4


def test_report_lists_the_metrics_and_the_alignment_table(tmp_path, mock):
    truths = [TRUTH[i] for i in ("inv_003", "inv_013", "inv_018")]
    ev.process(truths, tmp_path / "r", mock, resume=False)
    docs = ev.score_all(truths, tmp_path / "r")
    report = ev.render_report(docs, mock=True, model="mock", generated="x", injected=mock.injected, partial=True)
    for text in (
        "Auto-accepted",
        "Correct straight-through",
        "Bad straight-through",
        "Touched by a human",
        "Planted problems passed silently",
        "By position",
        "By content",
    ):
        assert text in report
    s = sc.variant_stats(docs, "pipeline")
    assert (s["auto"], s["correct_stp"], s["bad_stp"]) == (3, 1, 2)  # inv_013 and inv_018 are silent errors
    b = sc.variant_stats(docs, "baseline")
    assert (b["auto"], b["bad_stp"]) == (3, 2)


def test_baseline_and_pipeline_share_one_extraction(tmp_path, mock):
    truths = [TRUTH["inv_002"]]  # first reply is cut-off JSON: the repair round serves both variants
    ev.process(truths, tmp_path / "r", mock, resume=False)
    d = ev.score_all(truths, tmp_path / "r")[0]
    assert d.repaired and not d.failed_baseline
    assert [c.correct for c in d.pipeline] == [c.correct for c in d.baseline]


def test_planted_reason_states():
    t = TRUTH["inv_011"]
    miss = sc.score_doc(t, {"record": copy.deepcopy(t["fields"]), "low": {}, "doc_type": "invoice"})
    assert sc.planted_report([miss])["flags"][0]["state"] == "missed"
    other = sc.score_doc(
        t, {"record": copy.deepcopy(t["fields"]), "low": {"invoice_date": ["not_in_source"]}, "doc_type": "invoice"}
    )
    row = sc.planted_report([other])["flags"][0]
    assert row["state"] == "other_reason" and row["matched"] == ["not_in_source"]
    hit = sc.score_doc(
        t,
        {"record": copy.deepcopy(t["fields"]), "low": {"invoice_date": ["validation:missing"]}, "doc_type": "invoice"},
    )
    assert sc.planted_report([hit])["flags"][0]["state"] == "hit"
    failed = sc.score_doc(t, {"record": None, "low": {}, "doc_type": None})
    assert sc.planted_report([failed])["flags"][0]["state"] == "failed"


def test_an_invented_invoice_date_on_a_document_with_none_is_flagged_for_another_reason():
    res, d = attack("inv_011", setp("invoice_date", "2026-01-01"))
    low = res.fields["invoice_date"]["reasons"]
    assert low == ["not_in_source"]  # flagged, but not by the missing-date rule
    assert sc.planted_report([d])["flags"][0]["state"] == "other_reason"


def test_a_crash_on_one_document_is_recorded_and_the_run_continues(tmp_path, mock, monkeypatch):
    real = pipeline.ingest

    def flaky(db, data, filename, chat=None, keep_file=True):
        if filename == "inv_003.pdf":
            raise RuntimeError("boom")
        return real(db, data, filename, chat, keep_file)

    monkeypatch.setattr(ev.pipeline, "ingest", flaky)
    truths = [TRUTH["inv_002"], TRUTH["inv_003"], TRUTH["inv_004"]]
    ev.process(truths, tmp_path / "r", mock, resume=False)
    saved = {t["id"]: json.loads((tmp_path / "r" / f"{t['id']}.json").read_text("utf-8")) for t in truths}
    assert saved["inv_003"]["failed"] and "RuntimeError: boom" in saved["inv_003"]["error"]
    assert not saved["inv_002"]["failed"] and not saved["inv_004"]["failed"]
    docs = ev.score_all(truths, tmp_path / "r")
    assert [d.failed_pipeline for d in docs] == [False, True, False]
