"""Synthetic passport pages for the MRZ tests.

The data is the ICAO 9303 specimen document, not a real person's passport.
Pages are rendered locally, so no real passport image is stored in the tests.
"""

from __future__ import annotations

import io
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SPECIMEN_LINE_1 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"
SPECIMEN_LINE_2 = "L898902C36UTO7408122F1204159ZE184226B<<<<<10"
# Same specimen with the document-number check digit changed from 6 to 7.
BAD_CHECK_DIGIT_LINE_2 = "L898902C37UTO7408122F1204159ZE184226B<<<<<10"
PAGE_SIZE = (1800, 1100)
FONT_SIZE = 32
FONT_CANDIDATES = (
    Path(r"C:\Windows\Fonts\lucon.ttf"),
    Path(r"C:\Windows\Fonts\consola.ttf"),
    Path(r"C:\Windows\Fonts\cour.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    Path("/System/Library/Fonts/Menlo.ttc"),
)


def monospace_font() -> ImageFont.FreeTypeFont | None:
    for path in FONT_CANDIDATES:
        if not path.exists():
            continue
        try:
            return ImageFont.truetype(str(path), FONT_SIZE)
        except OSError:
            continue
    return None


def passport_page(
    line_2: str = SPECIMEN_LINE_2, printed_lines: tuple[str, ...] = ()
) -> Image.Image:
    font = monospace_font()
    if font is None:
        raise AssertionError("no monospace font available for the test fixtures")
    page = Image.new("RGB", PAGE_SIZE, "white")
    draw = ImageDraw.Draw(page)
    for offset, text in enumerate(printed_lines):
        draw.text((40, 60 + offset * 36), text, font=font, fill="black")
    draw.text((40, 900), SPECIMEN_LINE_1, font=font, fill="black")
    draw.text((40, 943), line_2, font=font, fill="black")
    return page


def rotated(page: Image.Image, degrees: int) -> Image.Image:
    """Turn the page clockwise, the way a sideways photo of it would."""
    return page if degrees == 0 else page.rotate(-degrees, expand=True)


def png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg_bytes(image: Image.Image, quality: int = 85) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def noise_image(seed: int = 7, size: tuple[int, int] = (1400, 1000)) -> Image.Image:
    generator = random.Random(seed)
    pixels = bytes(generator.randrange(256) for _ in range(size[0] * size[1] * 3))
    return Image.frombytes("RGB", size, pixels)
