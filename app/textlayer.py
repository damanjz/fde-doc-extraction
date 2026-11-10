"""Step 1: get text (and line confidences) out of an uploaded PDF or image.

PDF pages use the embedded text layer (pypdf, layout mode). A page without usable text is OCR'd with
rapidocr: scanned PDFs have one image per page, which is extracted and read.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageOps
from pypdf import PdfReader

from app import ocr

Image.MAX_IMAGE_PIXELS = 2 * 40_000_000  # Pillow's own hard stop; our check above is the one that reports

MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 10
MIN_TEXT_CHARS = 30  # alphanumeric characters for a page to count as having a text layer
MAX_PIXELS = 40_000_000  # per image, checked from the header before anything is decoded
MAX_TOTAL_PIXELS = 80_000_000  # per document, over the page images that get OCR'd
MAX_IMAGES_PER_PAGE = 8
MAX_SIDE = 3000


class UploadError(Exception):
    """Rejected input. status is the HTTP status the API returns."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Line:
    text: str
    conf: float | None  # None for text-layer lines
    segs: list[tuple[str, float]] = field(default_factory=list)
    # (segment text, start column in characters): the cells of a row, used to tell a wrapped cell from a whole one
    cols: list[tuple[str, float]] = field(default_factory=list)
    blank_before: bool = False  # a blank line separated this line from the one above


@dataclass
class Page:
    number: int
    source: str  # pdf | ocr
    text: str
    lines: list[Line]


@dataclass
class TextDoc:
    kind: str  # pdf | png | jpeg
    pages: list[Page]
    ocr_ms: int = 0

    @property
    def text(self) -> str:
        if len(self.pages) == 1:
            return self.pages[0].text
        return "\n".join(f"--- page {p.number} of {len(self.pages)} ---\n{p.text}" for p in self.pages)

    @property
    def lines(self) -> list[Line]:
        return [ln for p in self.pages for ln in p.lines]

    @property
    def is_scan(self) -> bool:
        return any(p.source == "ocr" for p in self.pages)

    @property
    def mean_ocr_conf(self) -> float | None:
        confs = [ln.conf for ln in self.lines if ln.conf is not None]
        return sum(confs) / len(confs) if confs else None


def sniff(data: bytes) -> str | None:
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    return None


def tidy(text: str) -> str:
    """Trim trailing blanks, squeeze long runs of spaces (keep 3 so columns stay visible) and blank lines."""
    out, blank = [], False
    for raw in text.splitlines():
        ln = re.sub(r"[ \t]{4,}", "   ", raw.rstrip())
        ln = ln.lstrip() if ln.strip() else ""
        if not ln:
            if not blank and out:
                out.append("")
            blank = True
            continue
        blank = False
        out.append(ln)
    return "\n".join(out).strip()


_CELL = re.compile(r"\S(?:.*?\S)??(?=\s{2,}|\s*$)")  # lazy: a cell ends at the first gap of 2+ spaces


def layout_lines(raw: str) -> list[Line]:
    """Lines of a pypdf layout-mode page with their cells and start columns (before spaces are squeezed)."""
    out: list[Line] = []
    blank = False
    for ln in raw.splitlines():
        if not ln.strip():
            blank = True
            continue
        cells = [(m.group(0), float(m.start())) for m in _CELL.finditer(ln.rstrip())]
        out.append(Line("   ".join(c for c, _ in cells), None, cols=cells, blank_before=blank))
        blank = False
    return out


def _ocr_page(number: int, pil: Image.Image) -> Page:
    pil = ImageOps.exif_transpose(pil).convert("RGB")
    if max(pil.size) > MAX_SIDE:
        r = MAX_SIDE / max(pil.size)
        pil = pil.resize((round(pil.width * r), round(pil.height * r)), Image.LANCZOS)
    lines = ocr.ocr_image(np.asarray(pil))
    return Page(
        number,
        "ocr",
        tidy("\n".join(ln.text for ln in lines)),
        [
            Line(ln.text, ln.conf, [(s.text, s.conf) for s in ln.segs], [(s.text, s.col) for s in ln.segs])
            for ln in lines
        ],
    )


def _check_page_images(page) -> list[tuple[str, int]]:
    """Image names and pixel counts of a page, read from the PDF dictionaries without decoding anything.

    Rejects a page with too many images, an image too large, or images nested in form XObjects (their size
    cannot be read before decoding).
    """
    names = list(page.images.keys())
    if len(names) > MAX_IMAGES_PER_PAGE:
        raise UploadError(422, f"{len(names)} images on one page; the limit is {MAX_IMAGES_PER_PAGE}")
    resources = page.get("/Resources")
    xobjects = resources.get_object().get("/XObject") if resources is not None else None
    xobjects = xobjects.get_object() if xobjects is not None else {}
    out = []
    for name in names:
        obj = xobjects.get(name)
        if obj is None:
            raise UploadError(422, "images nested inside other objects are not supported")
        d = obj.get_object()
        try:
            w, h = int(d["/Width"]), int(d["/Height"])
        except (KeyError, TypeError, ValueError) as exc:
            raise UploadError(422, "image without a readable size") from exc
        if w <= 0 or h <= 0 or w * h > MAX_PIXELS:
            raise UploadError(422, f"image of {w}x{h} pixels is too large (limit {MAX_PIXELS // 1_000_000} megapixels)")
        out.append((name, w * h))
    return out


def read_document(data: bytes) -> TextDoc:
    """Raise UploadError for anything that is not a readable PDF, PNG or JPEG within the limits."""
    import time

    if not data:
        raise UploadError(400, "empty file")
    if len(data) > MAX_BYTES:
        raise UploadError(413, f"file larger than {MAX_BYTES // (1024 * 1024)} MB")
    kind = sniff(data)
    if kind is None:
        raise UploadError(415, "unsupported file type: send a PDF, PNG or JPEG")
    t0 = time.monotonic()
    pages: list[Page] = []
    if kind == "pdf":
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise UploadError(422, "encrypted PDF")
            n = len(reader.pages)
            if n > MAX_PAGES:
                raise UploadError(422, f"{n} pages; the limit is {MAX_PAGES}")
            total_px = 0
            for i, page in enumerate(reader.pages, 1):
                text = page.extract_text(extraction_mode="layout") or ""
                if sum(ch.isalnum() for ch in text) >= MIN_TEXT_CHARS:
                    t = tidy(text)
                    pages.append(Page(i, "pdf", t, layout_lines(text)))
                    continue
                sizes = _check_page_images(page)
                if not sizes:
                    pages.append(Page(i, "pdf", "", []))
                    continue
                name, px = max(sizes, key=lambda s: s[1])
                total_px += px
                if total_px > MAX_TOTAL_PIXELS:
                    raise UploadError(422, "the page images of this document are too large in total")
                pages.append(_ocr_page(i, page.images[name].image))
        except UploadError:
            raise
        except Exception as exc:  # malformed PDFs fail in many ways; every one is a client error
            raise UploadError(422, f"unreadable PDF: {type(exc).__name__}") from exc
    else:
        try:
            img = Image.open(io.BytesIO(data))
            if img.width * img.height > MAX_PIXELS:  # header only: nothing decoded yet
                raise UploadError(422, f"image of {img.width}x{img.height} pixels is too large")
            img.load()
        except UploadError:
            raise
        except Exception as exc:
            raise UploadError(422, f"unreadable image: {type(exc).__name__}") from exc
        try:
            pages.append(_ocr_page(1, img))
        except Exception as exc:
            raise UploadError(422, f"could not read the image: {type(exc).__name__}") from exc
    return TextDoc(kind, pages, ocr_ms=int((time.monotonic() - t0) * 1000))
