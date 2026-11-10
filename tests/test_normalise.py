from datetime import date
from decimal import Decimal

import pytest

from app import normalise as n


@pytest.mark.parametrize(
    "raw,want",
    [
        ("1,23,456.50", "123456.50"),
        ("123,456.50", "123456.50"),
        ("₹ 4,05,450.75", "405450.75"),
        ("Rs. 1,000", "1000"),
        ("INR 2,500.00", "2500.00"),
        (12.5, "12.5"),
        (7, "7"),
        ("(1,200.00)", "-1200.00"),
        ("12 345", "12345"),
    ],
)
def test_parse_decimal(raw, want):
    assert n.parse_decimal(raw) == Decimal(want)


@pytest.mark.parametrize("raw", ["", "abc", "1,37.683.00", "12O", None, True, "1.2.3"])
def test_parse_decimal_rejects(raw):
    assert n.parse_decimal(raw) is None


@pytest.mark.parametrize(
    "raw,want",
    [
        ("2026-08-04", date(2026, 8, 4)),
        ("04/08/2026", date(2026, 8, 4)),
        ("04-08-2026", date(2026, 8, 4)),
        ("04.08.2026", date(2026, 8, 4)),
        ("04-Aug-2026", date(2026, 8, 4)),
        ("4 August 2026", date(2026, 8, 4)),
        ("4 Aug 2026", date(2026, 8, 4)),
        ("04/08/26", date(2026, 8, 4)),
        ("31/12/2026", date(2026, 12, 31)),
    ],
)
def test_parse_date_is_day_first(raw, want):
    assert n.parse_date(raw) == want


@pytest.mark.parametrize(
    "raw", ["31/02/2026", "2026-13-01", "13/13/2026", "not a date", "", "Due on 04/08/2026 sharp", None, 5]
)
def test_parse_date_rejects(raw):
    assert n.parse_date(raw) is None


def test_find_dates_collects_every_format():
    text = "Invoice Date 29-Oct-2026  PO dt 17-Oct-2026  Due 2026-11-13  Pay by 13.11.2026 and 5/1/2026"
    assert n.find_dates(text) == {"2026-10-29", "2026-10-17", "2026-11-13", "2026-01-05"}


def test_number_tokens_lakh_and_western():
    got = n.number_tokens("Total 1,23,456.00 and 123,456.00 and 7 and 0.5 KRE/26-27/0711")
    assert Decimal("123456") in got and Decimal("7") in got and Decimal("0.5") in got and Decimal("711") in got


def test_squash_and_name_key():
    assert n.squash("Kaveri Ridge, Estates Pvt. Ltd") == "kaveriridgeestatespvtltd"
    assert n.name_key("  Kaveri   Ridge ") == "kaveriridge"
    assert n.name_key("Pvt. Ltd") != n.name_key("Pvt Ltd")  # punctuation matters for names


def test_canonical_value_kinds():
    assert n.canonical_value("gstin", "27 aapfu0939f1zv") == ("27AAPFU0939F1ZV", True)
    assert n.canonical_value("date", "04-Aug-2026") == ("2026-08-04", True)
    assert n.canonical_value("date", "sometime") == ("sometime", False)
    assert n.canonical_value("amount", "₹ 1,23,456.5") == (123456.5, True)
    assert n.canonical_value("qty", "125.5000") == (125.5, True)
    assert n.canonical_value("int", "12") == (12, True)
    assert n.canonical_value("int", "12.5") == (12.5, False)
    assert n.canonical_value("currency", "Rs.") == ("INR", True)
    assert n.canonical_value("currency", "usd") == ("USD", True)
    assert n.canonical_value("name", "  ") == (None, True)
    assert n.canonical_value("id", 4021.0) == ("4021", True)
    assert n.canonical_value("amount", None) == (None, True)


def test_canonical_invoice_record_and_bad_paths():
    raw = {
        "supplier_name": "A",
        "invoice_date": "banana",
        "total": "1,000",
        "line_items": [{"description": "x", "qty": "2", "unit_price": "5", "amount": "ten"}, "junk"],
    }
    rec, bad = n.canonical("invoice", raw)
    assert rec["total"] == 1000.0 and rec["invoice_date"] == "banana"
    assert bad == {"invoice_date", "line_items[0].amount", "line_items"}
    assert len(rec["line_items"]) == 1


def test_canonical_line_items_not_a_list():
    rec, bad = n.canonical("invoice", {"line_items": "none"})
    assert rec["line_items"] == [] and "line_items" in bad
    rec, bad = n.canonical("invoice", {})
    assert rec["line_items"] == [] and not bad
