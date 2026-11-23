"""Local text extraction for uploaded images and PDFs.

Everything runs offline: Tesseract for images and scanned PDF pages, pypdf for
PDF text layers, PyMuPDF to render pages (no poppler needed).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pymupdf
import pytesseract
from PIL import Image, ImageOps
from pypdf import PdfReader

PAGE_OCR_CONFIG = "--oem 1 --psm 6"
LINE_OCR_CONFIG = "--oem 1 --psm 7"
MRZ_WHITELIST = "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"
BOTTOM_CROP_RATIO = 0.3
MIN_LINE_HEIGHT = 12
DARK_PIXEL_THRESHOLD = 160
MIN_IMAGE_WIDTH = 1600
RENDER_DPI = 300
MIN_TEXT_LAYER_CHARS = 40


class OcrError(RuntimeError):
    """The document could not be read."""


@dataclass(frozen=True)
class OcrResult:
    text: str
    mrz_text: str
    pages: int
    method: str


def read_document(filename: str, data: bytes) -> OcrResult:
    """Return OCR text for ``data``, plus a machine-readable pass on the MRZ area."""
    try:
        if Path(filename).suffix.lower() == ".pdf":
            return _read_pdf(data)
        return _read_image(data)
    except pytesseract.TesseractNotFoundError as exc:
        raise OcrError(
            "Tesseract OCR is not available. Check the Tesseract installation and PATH."
        ) from exc
    except OcrError:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced to the employee as a message
        raise OcrError(f"Could not read this document: {exc}") from exc


def _read_image(data: bytes) -> OcrResult:
    image = Image.open(io.BytesIO(data))
    text = _ocr_text(_prepare(image))
    mrz_text = _ocr_mrz(_bottom_part(image))
    return OcrResult(text=text, mrz_text=mrz_text, pages=1, method="image")


def _read_pdf(data: bytes) -> OcrResult:
    layer_text, page_count = _pdf_text_layer(data)
    if len(layer_text.strip()) >= MIN_TEXT_LAYER_CHARS:
        return OcrResult(
            text=layer_text,
            mrz_text=layer_text,
            pages=page_count,
            method="pdf text layer",
        )

    texts: list[str] = []
    mrz_text = ""
    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        for index in range(document.page_count):
            page = document.load_page(index)
            pixmap = page.get_pixmap(dpi=RENDER_DPI)
            image = Image.open(io.BytesIO(pixmap.tobytes("png")))
            prepared = _prepare(image)
            texts.append(_ocr_text(prepared))
            page_mrz = _ocr_mrz(_bottom_part(prepared))
            if page_mrz.strip():
                mrz_text = page_mrz
    finally:
        document.close()

    return OcrResult(
        text="\n".join(texts),
        mrz_text=mrz_text,
        pages=len(texts),
        method="pdf rendered",
    )


def _pdf_text_layer(data: bytes) -> tuple[str, int]:
    reader = PdfReader(io.BytesIO(data))
    texts = [(page.extract_text() or "") for page in reader.pages]
    return "\n".join(texts), len(reader.pages)


def _prepare(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image).convert("RGB")
    if image.width < MIN_IMAGE_WIDTH:
        scale = MIN_IMAGE_WIDTH / image.width
        size = (int(image.width * scale), int(image.height * scale))
        image = image.resize(size, Image.Resampling.LANCZOS)
    return image


def _bottom_part(image: Image.Image) -> Image.Image:
    height = max(1, int(image.height * BOTTOM_CROP_RATIO))
    return image.crop((0, image.height - height, image.width, image.height))


def _ocr_text(image: Image.Image) -> str:
    return pytesseract.image_to_string(image, lang="eng", config=PAGE_OCR_CONFIG)


def _ocr_mrz(image: Image.Image) -> str:
    """Read the two MRZ lines, falling back to a whole-block pass."""
    try:
        lines = _read_mrz_lines(image)
    except Exception:  # noqa: BLE001 - the block pass below is the fallback
        lines = []
    if lines:
        return "\n".join(lines)
    return pytesseract.image_to_string(
        image, lang="eng", config=f"{PAGE_OCR_CONFIG} {MRZ_WHITELIST}"
    )


def _read_mrz_lines(image: Image.Image) -> list[str]:
    """OCR the last two text bands of the MRZ area as single lines."""
    grey = image.convert("L")
    spans = [span for span in _text_bands(grey) if span[1] - span[0] >= MIN_LINE_HEIGHT]
    if len(spans) < 2:
        return []
    lines: list[str] = []
    for start, end in spans[-2:]:
        line = grey.crop((0, max(0, start - 4), grey.width, min(grey.height, end + 4)))
        line = line.resize((line.width * 2, line.height * 2), Image.Resampling.LANCZOS)
        line = ImageOps.autocontrast(line)
        text = pytesseract.image_to_string(
            line, lang="eng", config=f"{LINE_OCR_CONFIG} {MRZ_WHITELIST}"
        )
        compact = "".join(text.split())
        if not compact:
            return []
        lines.append(compact)
    return lines


def _text_bands(image: Image.Image) -> list[tuple[int, int]]:
    """Vertical spans of rows that contain dark pixels."""
    binary = image.point(
        lambda pixel: 255 if pixel < DARK_PIXEL_THRESHOLD else 0
    )
    profile = binary.resize((1, binary.height), Image.Resampling.BOX)
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for y, value in enumerate(profile.tobytes()):
        if value and start is None:
            start = y
        elif not value and start is not None:
            spans.append((start, y))
            start = None
    if start is not None:
        spans.append((start, binary.height))
    return spans
