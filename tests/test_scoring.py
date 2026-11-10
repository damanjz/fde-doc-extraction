import copy

import pytest

from evals import scoring as sc

TRUTH = {
    "id": "inv_x",
    "type": "invoice",
    "source": "pdf",
    "planted": [],
    "expected_flags": [],
    "expected_warnings": [],
    "fields": {
        "supplier_name": "Kaveri Ridge Estates Pvt Ltd",
        "supplier_gstin": "29AABCK3381M1Z4",
        "invoice_number": "K/1",
        "invoice_date": "2026-08-04",
        "due_date": None,
        "line_items": [
            {"description": "A", "qty": 2.0, "unit_price": 5.0, "amount": 10.0},
            {"description": "B", "qty": 1.0, "unit_price": 7.5, "amount": 7.5},
        ],
        "subtotal": 17.5,
        "gst_rate": 18.0,
        "gst_amount": 3.15,
        "total": 20.65,
        "currency": "INR",
    },
}


def rec():
    return copy.deepcopy(TRUTH["fields"])


def cells(record, low=None, truth=TRUTH, failed=False):
    return {c.path: c for c in sc.cells_for(truth, record, low or {}, failed)}


@pytest.mark.parametrize(
    "kind,t,p,ok",
    [
        ("name", "Kaveri  Ridge", "kaveri ridge", True),
        ("name", "Pvt. Ltd", "Pvt Ltd", False),
        ("amount", 10.0, 10.004, True),
        ("amount", 10.0, 10.01, False),
        ("qty", 1.5, 1.5004, True),
        ("date", "2026-08-04", "2026-08-04", True),
        ("date", "2026-08-04", "2026-04-08", False),
        ("date", None, None, True),
        ("date", None, "2026-08-04", False),
        ("amount", 5, None, False),
        ("id", "K/1", "k/1", False),
        ("gstin", "29A", "29A", True),
        ("int", 7, 7, True),
        ("int", 7, 8, False),
        ("amount", 5, "n/a", False),
    ],
)
def test_equal(kind, t, p, ok):
    assert sc.equal(kind, t, p) is ok


def test_perfect_record_all_correct_none_flagged():
    cs = cells(rec())
    assert all(c.correct and not c.flagged for c in cs.values())
    assert len(cs) == 10 + 8  # 10 header fields + 2 lines x 4 cells


def test_wrong_cell_and_flag_state():
    r = rec()
    r["total"] = 99.0
    cs = cells(r, {"total": ["validation:total_mismatch"]})
    assert not cs["total"].correct and cs["total"].flagged
    r["subtotal"] = 1.0
    assert not cells(r)["subtotal"].correct and not cells(r)["subtotal"].flagged


def test_lines_align_by_position_missing_line_counts_its_cells_wrong():
    r = rec()
    r["line_items"].pop()
    cs = cells(r)
    assert all(not cs[f"line_items[1].{n}"].correct for n in ("description", "qty", "unit_price", "amount"))
    assert not cs["line_items[1].qty"].flagged
    assert cells(r, {"line_items": ["validation:subtotal_mismatch"]})["line_items[1].qty"].flagged


def test_extra_line_counts_its_cells_wrong():
    r = rec()
    r["line_items"].append({"description": "C", "qty": 1.0, "unit_price": 1.0, "amount": 1.0})
    cs = cells(r)
    assert len(cs) == 22 and not cs["line_items[2].amount"].correct


def test_swapped_lines_are_wrong_position_by_position():
    r = rec()
    r["line_items"].reverse()
    cs = cells(r)
    assert not cs["line_items[0].description"].correct and not cs["line_items[1].amount"].correct


def test_failed_document_is_all_wrong_and_all_flagged_for_the_pipeline():
    cs = cells(None, failed=True)
    assert all(not c.correct and c.flagged for c in cs.values())
    cs = cells(None, failed=False)
    assert all(not c.correct and not c.flagged for c in cs.values())


def test_planted_cells_are_marked_from_expected_flags_and_related():
    t = copy.deepcopy(TRUTH)
    t["planted"] = ["total_off"]
    t["expected_flags"] = [{"field": "total", "reason": "total_mismatch", "related": ["subtotal", "gst_amount"]}]
    cs = cells(rec(), truth=t)
    assert {p for p, c in cs.items() if c.planted} == {"total", "subtotal", "gst_amount"}


def doc(record, low, truth=TRUTH, warnings=None):
    return sc.score_doc(
        truth,
        {
            "record": record,
            "low": low,
            "warnings": warnings or [],
            "doc_type": truth["type"],
            "timings": {"total_ms": 2000, "text_ms": 100, "llm_ms": 900},
        },
    )


def test_silent_error_rate_and_document_outcomes():
    bad = rec()
    bad["total"] = 1.0
    bad["supplier_name"] = "Other"
    docs = [doc(rec(), {}), doc(bad, {"total": ["x"]}), doc(rec(), {"currency": ["not_in_source"]})]
    p = sc.variant_stats(docs, "pipeline")
    b = sc.variant_stats(docs, "baseline")
    assert (p["cells"], p["wrong"], p["wrong_flagged"], p["silent"]) == (18 * 3, 2, 1, 1)
    # pipeline: doc 1 auto-accepted and right; docs 2 and 3 touched
    assert (p["auto"], p["correct_stp"], p["bad_stp"], p["touched"]) == (1, 1, 0, 2)
    # baseline: everything is auto-accepted, and the doc with wrong fields is a bad straight-through
    assert (b["auto"], b["correct_stp"], b["bad_stp"], b["touched"]) == (3, 2, 1, 0)
    assert b["silent"] == 2 and p["docs_with_silent"] == 1 and b["docs_with_silent"] == 1


def test_bad_straight_through_counts_an_auto_accepted_wrong_doc():
    bad = rec()
    bad["supplier_name"] = "Other"
    p = sc.variant_stats([doc(bad, {})], "pipeline")
    assert (p["auto"], p["correct_stp"], p["bad_stp"], p["silent"], p["docs_with_silent"]) == (1, 0, 1, 1, 1)


def test_planted_documents_never_count_as_correct_straight_through():
    t = copy.deepcopy(TRUTH)
    t["planted"] = ["gstin_invalid"]
    t["expected_flags"] = [{"field": "supplier_gstin", "reason": "gstin_checksum", "related": []}]
    passed = sc.variant_stats([doc(rec(), {}, t)], "pipeline")  # a planted problem that nothing flagged
    assert (passed["auto"], passed["correct_stp"], passed["bad_stp"], passed["planted_passed"]) == (1, 0, 1, 1)
    caught = sc.variant_stats([doc(rec(), {"supplier_gstin": ["validation:gstin_checksum"]}, t)], "pipeline")
    assert (caught["touched"], caught["planted_passed"]) == (1, 0)


def test_any_warning_is_a_human_touch_including_type_uncertain():
    assert sc.variant_stats([doc(rec(), {}, warnings=["instruction_in_document"])], "pipeline")["touched"] == 1
    assert sc.variant_stats([doc(rec(), {}, warnings=["document_type_uncertain"])], "pipeline")["touched"] == 1
    assert sc.variant_stats([doc(rec(), {})], "pipeline")["auto"] == 1


def test_flag_quality_recall_and_precision_leave_planted_cells_out():
    bad = rec()
    bad["total"], bad["invoice_date"] = 1.0, "2026-01-01"
    t = copy.deepcopy(TRUTH)
    t["planted"] = ["gstin_invalid"]
    t["expected_flags"] = [{"field": "supplier_gstin", "reason": "gstin_checksum", "related": []}]
    d = doc(
        bad,
        {"total": ["not_in_source"], "currency": ["not_in_source"], "supplier_gstin": ["validation:gstin_checksum"]},
        t,
    )
    q = sc.flag_quality([d])
    assert (q["wrong"], q["wrong_flagged"]) == (2, 1)  # total flagged, invoice_date silent
    assert (q["flagged"], q["flagged_wrong"]) == (2, 1)  # planted gstin excluded; currency is a false alarm
    assert q["missed"][0][:2] == ("inv_x", "invoice_date")
    assert q["false_alarm_reasons"]["not_in_source"] == 1


def test_planted_report_hit_and_miss():
    t = copy.deepcopy(TRUTH)
    t["expected_flags"] = [
        {"field": "supplier_gstin", "reason": "gstin_checksum", "related": []},
        {"field": "total", "reason": "total_mismatch", "related": []},
    ]
    t["expected_warnings"] = ["instruction_in_document"]
    d = doc(rec(), {"supplier_gstin": ["x"]}, t, warnings=["instruction_in_document"])
    rep = sc.planted_report([d])
    assert [r["state"] for r in rep["flags"]] == ["other_reason", "missed"]  # "x" is not the expected rule
    d = doc(rec(), {"supplier_gstin": ["validation:gstin_checksum"]}, t, warnings=["instruction_in_document"])
    rep = sc.planted_report([d])
    assert [r["state"] for r in rep["flags"]] == ["hit", "missed"] and rep["flags"][0]["matched"]
    assert rep["warnings"][0]["hit"] and rep["warnings"][0]["values_ok"]


def test_per_field_aggregates_line_cells_by_column():
    bad = rec()
    bad["line_items"][0]["amount"] = 1.0
    rows = {r["field"]: r for r in sc.per_field([doc(bad, {})])}
    assert rows["line_items.amount"] == {"field": "line_items.amount", "n": 2, "ok": 1, "flagged_wrong": 0, "silent": 1}


def test_latency_percentiles():
    assert sc.percentile([1, 2, 3, 4, 5], 0.5) == 3
    assert sc.percentile([1, 2, 3, 4, 5], 0.95) == pytest.approx(4.8)
    assert sc.percentile([], 0.5) == 0.0
    lat = sc.latency([doc(rec(), {})], "baseline")
    assert lat["p50"] == pytest.approx(1.0)
