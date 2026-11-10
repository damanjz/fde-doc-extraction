"""Page model with two backends: reportlab PDF (text layer) and Pillow raster (for scans).

Layouts draw into a Doc using points, y measured from the top of the page. The same primitives
feed the PDF writer and the rasteriser, so a scan is the same page as its text PDF, degraded.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

W, H = 595.28, 841.89
FONT_DIR = Path("C:/Windows/Fonts")
# logical name -> (file, ttc index)
FONTS: dict[str, tuple[str, int]] = {
    "arial": ("arial.ttf", 0),
    "arial-b": ("arialbd.ttf", 0),
    "calibri": ("calibri.ttf", 0),
    "calibri-b": ("calibrib.ttf", 0),
    "segoe": ("segoeui.ttf", 0),
    "segoe-b": ("segoeuib.ttf", 0),
    "times": ("times.ttf", 0),
    "times-b": ("timesbd.ttf", 0),
    "cour": ("cour.ttf", 0),
    "cour-b": ("courbd.ttf", 0),
}
_registered: set[str] = set()


def _register(font: str) -> None:
    if font in _registered:
        return
    path, idx = FONTS[font]
    full = FONT_DIR / path
    if not full.exists():
        raise FileNotFoundError(f"font {full} missing: the generator needs the Windows fonts it was written for")
    pdfmetrics.registerFont(TTFont(font, str(full), subfontIndex=idx))
    _registered.add(font)


def width(s: str, font: str, size: float) -> float:
    _register(font)
    return pdfmetrics.stringWidth(s, font, size)


@dataclass
class Prim:
    kind: str  # text | line | rect
    a: tuple = ()
    font: str = ""
    size: float = 0.0
    text: str = ""
    align: str = "l"
    gray: float = 0.0
    fill: float | None = None
    lw: float = 0.5


class Doc:
    """A multi-page document built from primitives."""

    def __init__(self) -> None:
        self.pages: list[list[Prim]] = [[]]

    @property
    def cur(self) -> list[Prim]:
        return self.pages[-1]

    def new_page(self) -> None:
        self.pages.append([])

    def text(
        self, x: float, y: float, s: str, font: str = "arial", size: float = 9, align: str = "l", gray: float = 0.0
    ) -> float:
        """Draw text with its baseline at y. Returns the text width."""
        if s == "":
            return 0.0
        self.cur.append(Prim("text", (x, y), font, size, s, align, gray))
        return width(s, font, size)

    def line(self, x1: float, y1: float, x2: float, y2: float, lw: float = 0.5, gray: float = 0.0) -> None:
        self.cur.append(Prim("line", (x1, y1, x2, y2), lw=lw, gray=gray))

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: float | None = None,
        lw: float = 0.5,
        gray: float = 0.0,
        stroke: bool = True,
    ) -> None:
        self.cur.append(Prim("rect", (x, y, w, h), gray=gray if stroke else -1.0, fill=fill, lw=lw))

    def texts(self) -> list[list[tuple[float, float, float, float, str]]]:
        """Per page: (x0, y_top, x1, y_bottom, text) boxes, for OCR accuracy checks."""
        out = []
        for page in self.pages:
            boxes = []
            for p in page:
                if p.kind != "text":
                    continue
                w = width(p.text, p.font, p.size)
                x, y = p.a
                x0 = x - w if p.align == "r" else x - w / 2 if p.align == "c" else x
                boxes.append((x0, y - p.size * 0.8, x0 + w, y + p.size * 0.2, p.text))
            out.append(boxes)
        return out


# --- PDF -------------------------------------------------------------------------------------------


def to_pdf(doc: Doc, path: Path, title: str = "") -> None:
    c = canvas.Canvas(str(path), pagesize=(W, H), pageCompression=1, invariant=1)
    c.setTitle(title)
    c.setAuthor("")
    c.setCreator("")
    c.setProducer("")
    for page in doc.pages:
        for p in page:
            if p.kind == "text":
                _register(p.font)
                c.setFont(p.font, p.size)
                c.setFillGray(p.gray)
                x, y = p.a
                y = H - y
                {"l": c.drawString, "r": c.drawRightString, "c": c.drawCentredString}[p.align](x, y, p.text)
            elif p.kind == "line":
                x1, y1, x2, y2 = p.a
                c.setLineWidth(p.lw)
                c.setStrokeGray(p.gray)
                c.line(x1, H - y1, x2, H - y2)
            else:
                x, y, w, h = p.a
                c.setLineWidth(p.lw)
                if p.fill is not None:
                    c.setFillGray(p.fill)
                c.setStrokeGray(max(p.gray, 0.0))
                c.rect(x, H - y - h, w, h, stroke=int(p.gray >= 0), fill=int(p.fill is not None))
        c.showPage()
    c.save()


# --- raster ----------------------------------------------------------------------------------------

_pil_fonts: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _pil_font(font: str, px: int) -> ImageFont.FreeTypeFont:
    key = (font, px)
    if key not in _pil_fonts:
        path, idx = FONTS[font]
        _pil_fonts[key] = ImageFont.truetype(str(FONT_DIR / path), px, index=idx)
    return _pil_fonts[key]


def to_images(doc: Doc, dpi: int = 150) -> list[Image.Image]:
    s = dpi / 72.0
    pages = []
    for page in doc.pages:
        img = Image.new("L", (round(W * s), round(H * s)), 255)
        d = ImageDraw.Draw(img)
        for p in page:
            if p.kind == "text":
                x, y = p.a
                anchor = {"l": "ls", "r": "rs", "c": "ms"}[p.align]
                d.text(
                    (x * s, y * s),
                    p.text,
                    font=_pil_font(p.font, max(1, round(p.size * s))),
                    anchor=anchor,
                    fill=round(p.gray * 255),
                )
            elif p.kind == "line":
                x1, y1, x2, y2 = p.a
                d.line((x1 * s, y1 * s, x2 * s, y2 * s), fill=round(p.gray * 255), width=max(1, round(p.lw * s)))
            else:
                x, y, w, h = p.a
                box = (x * s, y * s, (x + w) * s, (y + h) * s)
                if p.fill is not None:
                    d.rectangle(box, fill=round(p.fill * 255))
                if p.gray >= 0:
                    d.rectangle(box, outline=round(p.gray * 255), width=max(1, round(p.lw * s)))
        pages.append(img.convert("RGB"))
    return pages


@dataclass
class ScanParams:
    angle: float  # degrees, +-3 max
    noise: float  # gaussian sigma on 0-255
    blur: float  # Pillow gaussian radius at render resolution
    out_dpi: int  # final resolution (lower than render)
    jpeg_q: int | None  # None for PNG
    contrast: float = 0.92  # 1.0 = full contrast
    specks: int = 120


def scan_effect(img: Image.Image, p: ScanParams, rng: random.Random) -> Image.Image:
    """Make a page image look like a phone or flatbed scan."""
    paper = (rng.randint(240, 248), rng.randint(238, 246), rng.randint(228, 242))
    gray = np.asarray(img.convert("L"), dtype=np.float32) / 255.0
    ink = 0.04 + 0.10 * rng.random()
    page = np.empty(gray.shape + (3,), dtype=np.float32)
    ink_amt = 1.0 - gray  # 1 where there is ink
    for ch in range(3):
        page[..., ch] = paper[ch] * (1 - ink_amt * p.contrast) + (ink * 255) * ink_amt * p.contrast
    out = Image.fromarray(np.clip(page, 0, 255).astype(np.uint8))
    if p.blur > 0:
        out = out.filter(ImageFilter.GaussianBlur(p.blur))
    # soft shadow gradient along one edge
    h, w = gray.shape
    arr = np.asarray(out, dtype=np.float32)
    grad = np.linspace(rng.uniform(0.88, 0.97), 1.0, w, dtype=np.float32)[None, :, None]
    if rng.random() < 0.5:
        grad = grad[:, ::-1]
    arr = arr * grad
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    out = out.rotate(p.angle, resample=Image.BICUBIC, expand=True, fillcolor=tuple(round(c * 0.93) for c in paper))
    arr = np.asarray(out, dtype=np.float32)
    nrng = np.random.default_rng(rng.randint(0, 2**31 - 1))
    arr = arr + nrng.normal(0, p.noise, arr.shape[:2])[..., None]
    for _ in range(p.specks):  # dust
        y, x = nrng.integers(0, arr.shape[0]), nrng.integers(0, arr.shape[1])
        r = int(nrng.integers(1, 3))
        arr[y : y + r, x : x + r] *= 0.35
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    scale = p.out_dpi / (img.width / W * 72)
    if scale < 1:
        out = out.resize((round(out.width * scale), round(out.height * scale)), Image.LANCZOS)
    return out


def save_image(img: Image.Image, path: Path, jpeg_q: int | None) -> None:
    if jpeg_q is None:
        img.convert("L").save(path, format="PNG", optimize=True, dpi=(110, 110))
    else:
        img.save(path, format="JPEG", quality=jpeg_q, dpi=(110, 110))


def images_to_pdf(imgs: list[Image.Image], path: Path, jpeg_q: int = 70) -> None:
    """Image-only PDF (no text layer), as produced by a document scanner."""
    c = canvas.Canvas(str(path), pagesize=(W, H), pageCompression=1, invariant=1)
    c.setTitle("")
    for img in imgs:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=jpeg_q)
        buf.seek(0)
        from reportlab.lib.utils import ImageReader

        ratio = img.width / img.height
        pw, ph = (W, W / ratio) if ratio >= W / H else (H * ratio, H)
        c.drawImage(ImageReader(buf), 0, H - ph, width=pw, height=ph)
        c.showPage()
    c.save()
