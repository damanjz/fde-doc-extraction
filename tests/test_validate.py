import copy

import pytest

from app import gstin
from app import validate as v
from tests.conftest import TRUTH

GOOD_GSTIN = "29AABCK3381M1Z4"


def good_invoice() -> dict:
    return copy.deepcopy(TRUTH["inv_003"]["fields"])


def good_dn() -> dict:
    return copy.deepcopy(TRUTH["dn_001"]["fields"])


def rules(flags):
    return {(f.field, f.rule) for f in flags}


def test_clean_generated_invoice_has_no_flags():
    assert v.validate("invoice", good_invoice(), [], []) == []


def test_clean_generated_delivery_note_has_no_flags():
    assert v.validate("delivery_note", good_dn(), [], []) == []


# --- GSTIN ---------------------------------------------------------------------------------------


def test_gstin_checksum_flag():
    rec = good_invoice()
    last = rec["supplier_gstin"][14]
    rec["supplier_gstin"] = rec["supplier_gstin"][:14] + ("A" if last != "A" else "B")
    assert ("supplier_gstin", "gstin_checksum") in rules(v.validate("invoice", rec, [], []))


def test_gstin_format_flag():
    rec = good_invoice()
    rec["supplier_gstin"] = "12345"
    assert ("supplier_gstin", "gstin_format") in rules(v.validate("invoice", rec, [], []))


def test_gstin_missing_is_a_required_flag_not_a_gstin_flag():
    rec = good_invoice()
    rec["supplier_gstin"] = None
    r = rules(v.validate("invoice", rec, [], []))
    assert ("supplier_gstin", "missing") in r and ("supplier_gstin", "gstin_checksum") not in r


# --- required fields -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field", ["supplier_name", "invoice_number", "invoice_date", "subtotal", "gst_amount", "total"]
)
def test_required_invoice_fields(field):
    rec = good_invoice()
    rec[field] = None
    assert (field, "missing") in rules(v.validate("invoice", rec, [], []))


def test_no_line_items_flag():
    rec = good_invoice()
    rec["line_items"] = []
    assert ("line_items", "missing") in rules(v.validate("invoice", rec, [], []))


def test_due_date_is_optional():
    rec = good_invoice()
    rec["due_date"] = None
    assert v.validate("invoice", rec, [], []) == []


# --- dates ---------------------------------------------------------------------------------------


def test_due_before_invoice_flagged_on_both_fields():
    rec = good_invoice()
    rec["invoice_date"], rec["due_date"] = "2026-10-10", "2026-10-09"
    want = {("due_date", "due_before_invoice"), ("invoice_date", "due_before_invoice")}
    assert want <= rules(v.validate("invoice", rec, [], []))


def test_due_equal_to_invoice_date_is_fine():
    rec = good_invoice()
    rec["invoice_date"], rec["due_date"] = "2026-10-10", "2026-10-10"
    assert v.validate("invoice", rec, [], []) == []


def test_implausible_year():
    rec = good_invoice()
    rec["invoice_date"] = "1926-10-10"
    assert ("invoice_date", "date_range") in rules(v.validate("invoice", rec, [], []))


def test_unparseable_date_is_flagged_via_parse_errors():
    rec = good_invoice()
    rec["invoice_date"] = "soon"
    assert ("invoice_date", "unparseable") in rules(v.validate("invoice", rec, {"invoice_date"}, []))


# --- line amounts, subtotal, total, tax ------------------------------------------------------------


def test_line_amount_tolerance_boundary():
    rec = good_invoice()
    ln = rec["line_items"][0]
    ln["amount"] = round(ln["qty"] * ln["unit_price"] + 1.00, 2)
    assert v.check_lines(rec) == []  # exactly 1 INR off is inside the tolerance
    ln["amount"] = round(ln["qty"] * ln["unit_price"] + 1.02, 2)
    assert {f.field for f in v.check_lines(rec)} == {
        "line_items[0].qty",
        "line_items[0].unit_price",
        "line_items[0].amount",
    }


def test_line_non_positive_and_missing_cells():
    rec = good_invoice()
    rec["line_items"][1]["qty"] = 0
    assert ("line_items[1].qty", "non_positive") in rules(v.check_lines(rec))
    rec = good_invoice()
    rec["line_items"][0]["unit_price"] = None
    assert ("line_items[0].unit_price", "missing") in rules(v.check_lines(rec))


def test_subtotal_must_equal_sum_of_lines():
    rec = good_invoice()
    rec["subtotal"] += 5
    assert rules(v.check_subtotal(rec)) == {("subtotal", "subtotal_mismatch"), ("line_items", "subtotal_mismatch")}
    rec["subtotal"] = round(sum(x["amount"] for x in rec["line_items"]) + 0.99, 2)
    assert v.check_subtotal(rec) == []


def test_total_must_equal_subtotal_plus_tax():
    rec = good_invoice()
    rec["total"] += 1000
    assert rules(v.check_total(rec)) == {
        ("total", "total_mismatch"),
        ("subtotal", "total_mismatch"),
        ("gst_amount", "total_mismatch"),
    }


def test_total_allows_rounding_off_up_to_one_rupee():
    rec = good_invoice()
    rec["total"] = round(rec["subtotal"] + rec["gst_amount"] + 0.5, 2)
    assert v.check_total(rec) == []
    rec["total"] = round(rec["subtotal"] + rec["gst_amount"] + 1.5, 2)
    assert v.check_total(rec) != []


def test_gst_must_match_rate_times_subtotal():
    rec = good_invoice()
    rec["gst_amount"] += 50
    assert rules(v.check_tax(rec)) == {("gst_rate", "tax_mismatch"), ("gst_amount", "tax_mismatch")}


def test_gst_rate_must_be_a_slab():
    rec = good_invoice()
    rec["gst_rate"] = 17
    assert ("gst_rate", "gst_rate_slab") in rules(v.check_tax(rec))


def test_currency_rules():
    rec = good_invoice()
    rec["currency"] = None
    assert ("currency", "missing") in rules(v.check_currency(rec))
    rec["currency"] = "Rupees"
    assert ("currency", "currency_format") in rules(v.check_currency(rec))
    rec["currency"] = "INR"
    assert v.check_currency(rec) == []


def test_negative_and_zero_totals_flagged():
    rec = good_invoice()
    rec["total"] = -5
    assert ("total", "non_positive") in rules(v.check_positive(rec, ("subtotal", "total")))
    rec["total"] = 0
    assert ("total", "non_positive") in rules(v.check_positive(rec, ("total",)))


# --- duplicates ----------------------------------------------------------------------------------


def test_duplicate_invoice_same_gstin():
    rec = good_invoice()
    other = {
        "supplier_gstin": rec["supplier_gstin"],
        "supplier_name": "Other name",
        "invoice_number": rec["invoice_number"],
    }
    assert ("invoice_number", "duplicate_invoice_number") in rules(v.validate("invoice", rec, [], [other]))


def test_duplicate_invoice_same_name_when_gstin_unreadable():
    rec = good_invoice()
    other = {
        "supplier_gstin": "garbled",
        "supplier_name": rec["supplier_name"].upper(),
        "invoice_number": rec["invoice_number"].lower(),
    }
    assert v.check_duplicate_invoice(rec, [other])


def test_same_number_from_a_different_supplier_is_not_a_duplicate():
    rec = good_invoice()
    other = {"supplier_gstin": GOOD_GSTIN, "supplier_name": "Someone Else", "invoice_number": rec["invoice_number"]}
    assert v.check_duplicate_invoice(rec, [other]) == []


def test_duplicate_ignores_punctuation_in_number():
    rec = good_invoice()
    other = {"supplier_gstin": rec["supplier_gstin"], "invoice_number": rec["invoice_number"].replace("/", "-")}
    assert v.check_duplicate_invoice(rec, [other])


# --- delivery notes ------------------------------------------------------------------------------


def test_delivery_before_ship():
    rec = good_dn()
    rec["ship_date"], rec["delivery_date"] = "2026-08-18", "2026-08-16"
    want = {("delivery_date", "delivery_before_ship"), ("ship_date", "delivery_before_ship")}
    assert want <= rules(v.validate("delivery_note", rec, [], []))


def test_courier_gstin_checksum():
    rec = good_dn()
    good = gstin.make("36", "AAGCS8827N")
    rec["courier_gstin"] = good[:14] + ("0" if good[14] != "0" else "1")
    assert ("courier_gstin", "gstin_checksum") in rules(v.validate("delivery_note", rec, [], []))


@pytest.mark.parametrize(
    "awb,ok",
    [
        ("SLX313774146", True),
        ("205991766817", True),
        ("12AB", False),
        ("SLX-3137 74146", True),
        ("ABCDE12345678", False),
    ],
)
def test_awb_format(awb, ok):
    rec = good_dn()
    rec["awb_number"] = awb
    assert (v.check_awb_format(rec) == []) is ok


def test_packages_weight_positive_and_awb_duplicate():
    rec = good_dn()
    rec["packages"], rec["weight_kg"] = 0, -1
    assert {("packages", "non_positive"), ("weight_kg", "non_positive")} <= rules(
        v.validate("delivery_note", rec, [], [])
    )
    rec = good_dn()
    prior = [{"awb_number": rec["awb_number"].lower()}]
    assert ("awb_number", "duplicate_awb") in rules(v.validate("delivery_note", rec, [], prior))


def test_missing_required_delivery_field():
    rec = good_dn()
    rec["consignee"] = None
    assert ("consignee", "missing") in rules(v.validate("delivery_note", rec, [], []))


def test_flags_are_deduplicated():
    rec = good_invoice()
    rec["invoice_date"] = None
    flags = v.validate("invoice", rec, [], [])
    assert len(flags) == len(set(flags))
