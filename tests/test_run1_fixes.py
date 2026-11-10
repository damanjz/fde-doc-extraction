"""Changes after live run 1: only causes that generalise (OCR separators, glued GSTINs, the scorer's treatment of a
planted subtotal problem). The checks that caught real wrong fields must still catch them."""

import copy
from decimal import Decimal

import pytest

from app import confidence as c
from app.normalise import number_tokens
from app.textlayer import Line, Page, TextDoc
from evals import scoring as sc
from tests.conftest import TRUTH


def src(text: str) -> c.SourceIndex:
    return c.SourceIndex(TextDoc("png", [Page(1, "ocr", text, [Line(text, 0.9, [(text, 0.9)])])]))


# --- thousands separators misread as dots ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text,value",
    [
        ("Amount 22.158.00", "22158.00"),
        ("Total 1,37.683.00", "137683.00"),
        ("Taxable 3,08.073.50", "308073.50"),
        ("Subtotal 740.098.75", "740098.75"),
        ("Rate 6.740.25", "6740.25"),
        ("Amount 1,23,456.00", "123456.00"),
        ("Rs.181,328.00", "181328.00"),
    ],
)
def test_misread_thousands_separators_are_read_as_the_number_they_are(text, value):
    assert Decimal(value) in number_tokens(text)


@pytest.mark.parametrize("text", ["Dated 10.10.2026", "Ref 12.5", "Version 1.2.3", "Phone 4224391118", "HSN 09011112"])
def test_regrouping_does_not_invent_numbers_from_dates_versions_or_ids(text):
    assert not any(n in number_tokens(text) for n in (Decimal("1010.20"), Decimal("1.23"), Decimal("12345.67")))


def test_a_wrong_amount_is_still_not_found_next_to_a_misread_one():
    s = src("Amount   22.158.00")
    assert s.has("amount", 22158.0)
    for wrong in (22158.01, 22185.0, 2215.8, 221580.0, 22158.1):
        assert not s.has("amount", wrong), wrong


# --- GSTIN glued to a label or to the next word ------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "GSTIN33AAECS5126H1ZK   Due   10/09/2026",
        "GSTIN:29AABCK3381M1Z4PAN:AABCK3381M",
        "GST Reg.No.:36AAIFG7761E1Z9",
        "x 29aabck3381m1z4 y",
    ],
)
def test_a_gstin_is_found_when_ocr_glued_words_to_it(text):
    g = {
        "GSTIN33AAECS5126H1ZK   Due   10/09/2026": "33AAECS5126H1ZK",
        "GSTIN:29AABCK3381M1Z4PAN:AABCK3381M": "29AABCK3381M1Z4",
        "GST Reg.No.:36AAIFG7761E1Z9": "36AAIFG7761E1Z9",
        "x 29aabck3381m1z4 y": "29AABCK3381M1Z4",
    }[text]
    assert src(text).has("gstin", g)


def test_truncated_changed_or_ocr_damaged_gstins_are_still_not_found():
    s = src("GSTIN36AAIFG7761E129  GSTIN:29AABCK3381M1Z4PAN")
    assert not s.has("gstin", "36AAIFG7761E1Z9")  # the real number; the text has Z read as 2: the value is not there
    assert not s.has("gstin", "29AABCK3381M1Z")  # truncated
    assert not s.has("gstin", "29AABCK3381M1Z5")  # one character different
    assert not s.has("gstin", "9AABCK3381M1Z4")
    assert not s.has("gstin", "30AABCK3381M1Z4")


# --- scorer: a planted subtotal problem involves the whole line list ------------------------------------------


def test_planted_subtotal_problem_marks_every_line_cell_as_planted():
    t = TRUTH["scan_004"]
    cells = sc.cells_for(t, copy.deepcopy(t["fields"]), {}, False)
    line_cells = [x for x in cells if x.name.startswith("line_items.")]
    assert line_cells and all(x.planted for x in line_cells)
    assert next(x for x in cells if x.path == "subtotal").planted
    assert not next(x for x in cells if x.path == "invoice_number").planted


def test_line_cells_of_an_unplanted_document_are_not_planted():
    t = TRUTH["inv_003"]
    assert not any(x.planted for x in sc.cells_for(t, copy.deepcopy(t["fields"]), {}, False))


def test_per_document_flag_quality():
    t = TRUTH["inv_003"]
    ok = sc.score_doc(t, {"record": copy.deepcopy(t["fields"]), "low": {}, "doc_type": "invoice"})
    bad_rec = copy.deepcopy(t["fields"])
    bad_rec["total"] = 1.0
    bad_flagged = sc.score_doc(t, {"record": bad_rec, "low": {"total": ["x"]}, "doc_type": "invoice"})
    bad_silent = sc.score_doc(t, {"record": bad_rec, "low": {}, "doc_type": "invoice"})
    false_alarm = sc.score_doc(
        t, {"record": copy.deepcopy(t["fields"]), "low": {"currency": ["not_in_source"]}, "doc_type": "invoice"}
    )
    q = sc.doc_flag_quality([ok, bad_flagged, bad_silent, false_alarm])
    assert q == {"problem_docs": 2, "sent": 2, "both": 1}
