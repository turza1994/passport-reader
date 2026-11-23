"""Local text extraction for uploaded images and PDFs.

Everything runs offline: Tesseract for images and scanned PDF pages, pypdf for
PDF text layers, PyMuPDF to render pages (no poppler needed).
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pymupdf
import pytesseract
from PIL import Image, ImageOps
from pypdf import PdfReader

from core.passport import mrz_candidates, valid_mrz

PAGE_OCR_CONFIG = "--oem 1 --psm 6"
LINE_OCR_CONFIG = "--oem 1 --psm 7"
MRZ_WHITELIST = "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"
MRZ_BLOCK_CONFIG = f"{PAGE_OCR_CONFIG} {MRZ_WHITELIST}"
MRZ_LINE_CONFIG = f"{LINE_OCR_CONFIG} {MRZ_WHITELIST}"
MRZ_ROTATIONS = (0, 90, 180, 270)
MRZ_LINE_MIN_LENGTH = 40
MRZ_LINE_MAX_LENGTH = 46
MRZ_MAX_BANDS = 6
BAND_PADDING = 4
LINE_UPSCALE = 2
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
    mrz_rotation: int = 0


def read_document(filename: str, data: bytes) -> OcrResult:
    """Return OCR text for ``data``, plus a machine-readable pass on the MRZ."""
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
    image = _prepare(Image.open(io.BytesIO(data)))
    text = _ocr_text(image)
    mrz_text, rotation = _scan_mrz(image)
    if rotation:
        text = "\n".join([text, _ocr_text(_rotate(image, rotation))])
    return OcrResult(
        text=text,
        mrz_text=mrz_text,
        pages=1,
        method="image",
        mrz_rotation=rotation,
    )


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
    rotation = 0
    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        for index in range(document.page_count):
            page = document.load_page(index)
            pixmap = page.get_pixmap(dpi=RENDER_DPI)
            image = _prepare(Image.open(io.BytesIO(pixmap.tobytes("png"))))
            page_text = _ocr_text(image)
            page_mrz, page_rotation = _scan_mrz(image)
            if page_rotation:
                page_text = "\n".join([page_text, _ocr_text(_rotate(image, page_rotation))])
            texts.append(page_text)
            if page_mrz.strip():
                mrz_text = page_mrz
                rotation = page_rotation
    finally:
        document.close()

    return OcrResult(
        text="\n".join(texts),
        mrz_text=mrz_text,
        pages=len(texts),
        method="pdf rendered",
        mrz_rotation=rotation,
    )


def _pdf_text_layer(data: bytes) -> tuple[str, int]:
    reader = PdfReader(io.BytesIO(data))
    texts = [(page.extract_text() or "") for page in reader.pages]
    return "\n".join(texts), len(reader.pages)


def _prepare(image: Image.Image) -> Image.Image:
    return _ensure_width(ImageOps.exif_transpose(image).convert("RGB"))


def _ensure_width(image: Image.Image) -> Image.Image:
    if image.width >= MIN_IMAGE_WIDTH:
        return image
    scale = MIN_IMAGE_WIDTH / image.width
    size = (int(image.width * scale), int(image.height * scale))
    return image.resize(size, Image.Resampling.LANCZOS)


def _rotate(image: Image.Image, angle: int) -> Image.Image:
    """Turn a copy of ``image`` clockwise by ``angle`` degrees."""
    return _ensure_width(image.rotate(-angle, expand=True))


def _bottom_part(image: Image.Image) -> Image.Image:
    height = max(1, int(image.height * BOTTOM_CROP_RATIO))
    return image.crop((0, image.height - height, image.width, image.height))


def _ocr_text(image: Image.Image) -> str:
    return pytesseract.image_to_string(image, lang="eng", config=PAGE_OCR_CONFIG)


def _scan_mrz(image: Image.Image) -> tuple[str, int]:
    """Find a TD3 pair whose check digits validate, or give up.

    Passports are photographed upright, sideways or upside-down, so every
    right-angle orientation is tried. The bottom strip is scanned first because
    that is where the MRZ normally sits; the whole page follows so a loosely
    framed or unusually laid-out scan is still read. Each candidate must
    validate, and the search stops at the first one that does.
    """
    for angle in MRZ_ROTATIONS:
        oriented = image if angle == 0 else _rotate(image, angle)
        for region in (_bottom_part(oriented), oriented):
            pair = _valid_pair_in(region)
            if pair:
                return "\n".join(pair), angle
    return "", 0


def _valid_pair_in(region: Image.Image) -> list[str] | None:
    """Return the first check-digit-valid MRZ pair found in ``region``."""
    for lines in _mrz_line_pairs(region):
        if valid_mrz("\n".join(lines)):
            return lines
    for code in mrz_candidates(_mrz_block_text(region)):
        if valid_mrz(code):
            return code.splitlines()
    return None


def _mrz_line_pairs(region: Image.Image) -> Iterator[list[str]]:
    """OCR adjacent text bands as single MRZ lines, bottom pair first."""
    grey = region.convert("L")
    spans = [span for span in _text_bands(grey) if span[1] - span[0] >= MIN_LINE_HEIGHT]
    if len(spans) < 2:
        return
    bands = spans[-MRZ_MAX_BANDS:]
    for index in range(len(bands) - 2, -1, -1):
        first = _ocr_line(grey, bands[index])
        second = _ocr_line(grey, bands[index + 1])
        if _mrz_shaped(first) and _mrz_shaped(second):
            yield [first, second]


def _ocr_line(grey: Image.Image, span: tuple[int, int]) -> str:
    top = max(0, span[0] - BAND_PADDING)
    bottom = min(grey.height, span[1] + BAND_PADDING)
    line = grey.crop((0, top, grey.width, bottom))
    line = line.resize(
        (line.width * LINE_UPSCALE, line.height * LINE_UPSCALE),
        Image.Resampling.LANCZOS,
    )
    line = ImageOps.autocontrast(line)
    text = pytesseract.image_to_string(line, lang="eng", config=MRZ_LINE_CONFIG)
    return "".join(text.split())


def _mrz_shaped(line: str) -> bool:
    return MRZ_LINE_MIN_LENGTH <= len(line) <= MRZ_LINE_MAX_LENGTH


def _mrz_block_text(region: Image.Image) -> str:
    grey = ImageOps.autocontrast(region.convert("L"))
    return pytesseract.image_to_string(grey, lang="eng", config=MRZ_BLOCK_CONFIG)


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
