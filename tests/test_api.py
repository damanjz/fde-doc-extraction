import csv
import io

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from app import main
from app.textlayer import MAX_BYTES
from tests.conftest import TRUTH, doc_bytes


@pytest.fixture()
def client(db, mock):
    main.app.dependency_overrides[main.get_db] = lambda: db
    main.app.dependency_overrides[main.get_chat] = lambda: mock
    yield TestClient(main.app, follow_redirects=False)
    main.app.dependency_overrides.clear()


def upload(client, mock, doc_id, name=None):
    mock.current = doc_id
    return client.post(
        "/extract", files={"file": (name or TRUTH[doc_id]["file"], doc_bytes(doc_id), "application/pdf")}
    )


def form_for(page_fields: dict, **over) -> dict:
    """Submit the fields as the review form would (values unchanged), confirming every low-confidence field."""
    form = {}
    for path, f in page_fields.items():
        if path == "line_items":
            form["ok::line_items"] = "on"
            continue
        form[f"v::{path}"] = "" if f["value"] is None else str(f["value"])
        if f["confidence"] == "low":
            form[f"ok::{path}"] = "on"
    form.update(over)
    return form


def test_extract_returns_validated_json(client, mock):
    r = upload(client, mock, "inv_017")
    assert r.status_code == 200
    j = r.json()
    assert j["doc_type"] == "invoice" and j["review_url"] == f"/review/{j['id']}"
    assert j["record"]["total"] == TRUTH["inv_017"]["fields"]["total"]
    assert "total" in j["low_confidence"] and j["fields"]["total"]["confidence"] == "low"
    assert any(f["rule"] == "total_mismatch" for f in j["flags"])


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_review_page_shows_low_confidence_fields_and_the_document(client, mock):
    j = upload(client, mock, "inv_005").json()
    page = client.get(j["review_url"])
    assert page.status_code == 200
    body = page.text
    assert "Needs review" in body and "GSTIN checksum is wrong" in body
    assert f'src="/files/{j["id"]}"' in body and "Extracted text" in body
    assert "http://" not in body.replace("http://www.w3.org", "") and "cdn" not in body.lower()  # no external calls
    assert client.get(f"/files/{j['id']}").headers["content-type"] == "application/pdf"


def test_review_flow_accept_and_correct_then_export(client, mock):
    j = upload(client, mock, "inv_005").json()
    from app.gstin import check_char

    good = j["record"]["supplier_gstin"][:14] + check_char(j["record"]["supplier_gstin"][:14])
    form = form_for(j["fields"], **{"v::supplier_gstin": good})
    r = client.post(j["review_url"], data=form)
    assert r.status_code == 303 and r.headers["location"].endswith("?saved=1")
    assert "Decisions saved" in client.get(j["review_url"] + "?saved=1").text

    csv_text = client.get("/export.csv").text
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert len(rows) == 1 and rows[0]["supplier_gstin"] == good and rows[0]["status"] == "reviewed"
    assert rows[0]["corrected_fields"] == "supplier_gstin"
    dec = list(csv.DictReader(io.StringIO(client.get("/export/decisions.csv").text)))
    by_field = {d["field"]: d for d in dec}
    assert by_field["supplier_gstin"]["decision"] == "corrected"
    assert by_field["supplier_gstin"]["extracted_value"] == j["record"]["supplier_gstin"]
    assert by_field["invoice_number"]["decision"] == "accepted"


def test_unconfirmed_low_fields_keep_the_document_unreviewed(client, mock, db):
    j = upload(client, mock, "inv_005").json()
    form = form_for(j["fields"])
    del form["ok::supplier_gstin"]
    client.post(j["review_url"], data=form)
    assert db.get_document(j["id"])["status"] == "extracted"
    assert list(csv.DictReader(io.StringIO(client.get("/export.csv").text))) == []
    assert len(list(csv.DictReader(io.StringIO(client.get("/export.csv?all=1").text)))) == 1


def test_invalid_correction_is_rejected_without_saving(client, mock, db):
    j = upload(client, mock, "inv_017").json()
    r = client.post(j["review_url"], data=form_for(j["fields"], **{"v::total": "twelve"}))
    assert r.status_code == 422 and "not a valid amount" in r.text
    assert db.get_reviews(j["id"]) == {}


def test_correcting_a_line_cell_applies_to_the_export(client, mock):
    j = upload(client, mock, "inv_008").json()
    path = "line_items[2].amount"
    r = client.post(j["review_url"], data=form_for(j["fields"], **{f"v::{path}": "1,00,000.00"}))
    assert r.status_code == 303
    rows = list(csv.DictReader(io.StringIO(client.get("/export.csv?all=1").text)))
    assert '"amount": 100000.0' in rows[0]["line_items_json"]


def test_scan_review_page_uses_an_image(client, mock):
    mock.current = "scan_004"
    r = client.post("/extract", files={"file": ("scan.png", doc_bytes("scan_004"), "image/png")})
    assert r.status_code == 200 and r.json()["source"] == "scan"
    page = client.get(r.json()["review_url"]).text
    assert "<img " in page and "scan, OCR" in page


def test_csv_export_neutralises_spreadsheet_formulas(client, mock, db):
    j = upload(client, mock, "inv_003").json()
    r = client.post(j["review_url"], data=form_for(j["fields"], **{"v::supplier_name": '=HYPERLINK("http://x")'}))
    assert r.status_code == 303
    rows = list(csv.DictReader(io.StringIO(client.get("/export.csv").text)))
    assert rows[0]["supplier_name"].startswith("'=")


def test_html_is_escaped(client, mock):
    j = upload(client, mock, "inv_003", name="<script>alert(1)</script>.pdf").json()
    assert "<script>alert(1)" not in client.get(j["review_url"]).text
    assert "<script>alert(1)" not in client.get("/").text


# --- upload limits and bad input: 4xx, never a crash -------------------------------------------------


def post_bytes(client, data, name="x.pdf"):
    return client.post("/extract", files={"file": (name, data, "application/octet-stream")})


def test_wrong_type_is_415(client):
    assert post_bytes(client, b"hello, not a document", "x.txt").status_code == 415
    assert post_bytes(client, b"GIF89a....", "x.gif").status_code == 415


def test_empty_file_is_400(client):
    assert post_bytes(client, b"").status_code == 400


def test_too_large_is_413(client):
    assert post_bytes(client, b"%PDF-" + b"0" * MAX_BYTES).status_code == 413


def test_too_many_pages_is_422(client):
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for _ in range(11):
        c.drawString(72, 700, "page of an invoice with enough text to count as a text layer")
        c.showPage()
    c.save()
    r = post_bytes(client, buf.getvalue())
    assert r.status_code == 422 and "11 pages" in r.json()["detail"]


def test_corrupt_files_are_422(client):
    assert post_bytes(client, b"%PDF-1.4 this is not a pdf").status_code == 422
    assert post_bytes(client, b"\x89PNG\r\n\x1a\ngarbage").status_code == 422
    assert post_bytes(client, b"\xff\xd8\xff\xe0garbage").status_code == 422


def test_missing_file_field_is_422(client):
    assert client.post("/extract").status_code == 422


def test_a_file_disguised_by_extension_is_still_checked_by_magic_bytes(client):
    assert post_bytes(client, b"<html>hi</html>", "invoice.pdf").status_code == 415


def test_unknown_document_is_404(client):
    assert client.get("/review/999").status_code == 404
    assert client.post("/review/999", data={}).status_code == 404
    assert client.get("/files/999").status_code == 404


def test_llm_down_is_502_and_recorded(client, db):
    from app.llm import LLMError

    def down(_m):
        raise LLMError("ollama down")

    main.app.dependency_overrides[main.get_chat] = lambda: down
    r = post_bytes(client, doc_bytes("inv_003"))
    assert r.status_code == 502 and "ollama down" in r.json()["detail"]
    assert db.list_documents()[0]["status"] == "error"


def test_home_lists_documents(client, mock):
    upload(client, mock, "inv_003")
    assert "inv_003.pdf" in client.get("/").text
