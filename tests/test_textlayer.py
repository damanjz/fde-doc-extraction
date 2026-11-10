import io
from decimal import Decimal

import pytest
from reportlab.pdfgen import canvas

from app import textlayer as tl
from data.common import fmt_amount
from tests.conftest import TRUTH, doc_bytes


def make_pdf(pages: int, text: str = "Hello invoice number 12345 for Larkfield Coffee Company") -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for _ in range(pages):
        c.drawString(72, 700, text)
        c.showPage()
    c.save()
    return buf.getvalue()


def test_sniff_by_magic_bytes():
    assert tl.sniff(b"%PDF-1.7 ...") == "pdf"
    assert tl.sniff(b"\x89PNG\r\n\x1a\n....") == "png"
    assert tl.sniff(b"\xff\xd8\xff\xe0....") == "jpeg"
    assert tl.sniff(b"GIF89a") is None
    assert tl.sniff(b"MZ\x90\x00") is None


@pytest.mark.parametrize(
    "data,status",
    [
        (b"", 400),
        (b"just text", 415),
        (b"GIF89a....", 415),
        (b"%PDF-1.4 garbage that is not a pdf", 422),
        (b"\x89PNG\r\n\x1a\nnot really a png", 422),
        (b"\xff\xd8\xff\xe0 broken jpeg", 422),
    ],
)
def test_rejects_bad_uploads_with_a_client_error(data, status):
    with pytest.raises(tl.UploadError) as e:
        tl.read_document(data)
    assert e.value.status == status


def test_size_limit():
    with pytest.raises(tl.UploadError) as e:
        tl.read_document(b"%PDF-" + b"0" * tl.MAX_BYTES)
    assert e.value.status == 413


def test_page_limit_is_enforced_before_any_work():
    assert len(tl.read_document(make_pdf(tl.MAX_PAGES)).pages) == tl.MAX_PAGES
    with pytest.raises(tl.UploadError) as e:
        tl.read_document(make_pdf(tl.MAX_PAGES + 1))
    assert e.value.status == 422 and "pages" in e.value.message


def test_text_pdf_uses_the_text_layer_not_ocr():
    td = tl.read_document(doc_bytes("inv_003"))
    assert td.kind == "pdf" and not td.is_scan and td.mean_ocr_conf is None
    assert TRUTH["inv_003"]["fields"]["invoice_number"] in td.text
    assert TRUTH["inv_003"]["fields"]["supplier_gstin"] in td.text


def test_multi_page_pdf_keeps_page_markers_and_every_row():
    td = tl.read_document(doc_bytes("inv_001"))
    assert len(td.pages) == 2 and "--- page 2 of 2 ---" in td.text
    for ln in TRUTH["inv_001"]["fields"]["line_items"]:
        assert fmt_amount(Decimal(str(ln["amount"])), lakh=True) in td.pages[0].text
    assert str(TRUTH["inv_001"]["fields"]["invoice_number"]) in td.pages[1].text  # repeated in the continued header


def test_page_without_text_or_images_is_empty_not_an_error():
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.showPage()
    c.save()
    td = tl.read_document(buf.getvalue())
    assert td.pages[0].text == "" and td.pages[0].source == "pdf"


def test_tidy_squeezes_spaces_and_blank_lines():
    assert tl.tidy("a        b   \n\n\n\n  c  \n") == "a   b\n\nc"


@pytest.mark.ocr
def test_scanned_png_is_read_with_ocr_and_keeps_confidences():
    td = tl.read_document(doc_bytes("scan_004"))
    assert td.kind == "png" and td.is_scan and td.mean_ocr_conf and 0 < td.mean_ocr_conf <= 1
    assert "Net Payable" in td.text or "NetPayable" in td.text.replace(" ", "")
    assert all(ln.conf is not None and ln.segs for ln in td.lines)


@pytest.mark.ocr
def test_image_only_pdf_falls_back_to_ocr():
    td = tl.read_document(doc_bytes("scan_003"))
    assert td.kind == "pdf" and td.pages[0].source == "ocr" and td.is_scan
    assert "BBW" in td.text.upper().replace(" ", "") or "Invoice" in td.text
