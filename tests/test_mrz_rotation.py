"""Reading the MRZ from scans photographed at 0, 90, 180 and 270 degrees."""

from __future__ import annotations

import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from core import db, ocr, passport, statuses

from tests import mrz_pages

HAS_TESSERACT = shutil.which("tesseract") is not None
HAS_FONT = mrz_pages.monospace_font() is not None
requires_ocr = unittest.skipUnless(
    HAS_TESSERACT and HAS_FONT, "Tesseract and a monospace font are required"
)

EXPECTED_VALUES = {
    "surname": "ERIKSSON",
    "given_names": "ANNA MARIA",
    "passport_number": "L898902C3",
    "date_of_birth": "1974-08-12",
    "expiry_date": "2012-04-15",
    "nationality": "UTO",
    "passport_country": "UTO",
    "sex": "F",
}


def _read(image: Image.Image, *, suffix: str = "png") -> ocr.OcrResult:
    data = (
        mrz_pages.jpeg_bytes(image)
        if suffix == "jpg"
        else mrz_pages.png_bytes(image)
    )
    return ocr.read_document(f"passport.{suffix}", data)


class RotationTests(unittest.TestCase):
    @requires_ocr
    def test_all_four_orientations_are_read(self) -> None:
        page = mrz_pages.passport_page()
        for angle in (0, 90, 180, 270):
            with self.subTest(angle=angle):
                result = _read(mrz_pages.rotated(page, angle))
                self.assertTrue(result.mrz_text.strip())
                self.assertTrue(passport.valid_mrz(result.mrz_text))
                extraction = passport.extract_passport(result.text, result.mrz_text)
                for name, expected in EXPECTED_VALUES.items():
                    self.assertEqual(extraction.values.get(name), expected, name)
                self.assertFalse(
                    any("check digits did not validate" in note for note in extraction.notes)
                )

    @requires_ocr
    def test_sideways_scan_needs_the_rotation_search(self) -> None:
        sideways = mrz_pages.rotated(mrz_pages.passport_page(), 90)
        result = _read(sideways)
        self.assertNotEqual(result.mrz_rotation, 0)
        self.assertTrue(passport.valid_mrz(result.mrz_text))
        # Reading the sideways page as-is finds nothing, so the fields can only
        # come from the rotated MRZ pass or the rotated printed-page pass.
        unrotated_text = ocr._ocr_text(ocr._prepare(sideways))
        extraction = passport.extract_passport(unrotated_text, "")
        self.assertFalse({k: v for k, v in extraction.values.items() if v})

    @requires_ocr
    def test_rotated_photo_saves_status_and_printed_labels(self) -> None:
        page = mrz_pages.passport_page(printed_lines=("Place of birth DHAKA",))
        result = _read(mrz_pages.rotated(page, 270), suffix="jpg")
        extraction = passport.extract_passport(result.text, result.mrz_text)
        self.assertEqual(extraction.values.get("surname"), "ERIKSSON")
        self.assertEqual(extraction.values.get("place_of_birth"), "DHAKA")
        self.assertEqual(
            statuses.status_for(extraction.values, verified=False),
            statuses.NEEDS_REVIEW,
        )

    @requires_ocr
    def test_invalid_check_digits_are_never_accepted(self) -> None:
        page = mrz_pages.passport_page(line_2=mrz_pages.BAD_CHECK_DIGIT_LINE_2)
        for angle in (0, 90, 180, 270):
            with self.subTest(angle=angle):
                result = _read(mrz_pages.rotated(page, angle))
                self.assertEqual(result.mrz_text, "")
                extraction = passport.extract_passport(result.text, result.mrz_text)
                self.assertFalse({k: v for k, v in extraction.values.items() if v})
                self.assertTrue(extraction.notes)

    @requires_ocr
    def test_unreadable_image_falls_back_to_manual_entry(self) -> None:
        result = _read(mrz_pages.noise_image())
        self.assertEqual(result.mrz_text, "")
        extraction = passport.extract_passport(result.text, result.mrz_text)
        self.assertFalse({k: v for k, v in extraction.values.items() if v})
        self.assertTrue(extraction.notes)

    @requires_ocr
    def test_scanning_stops_at_the_first_valid_mrz(self) -> None:
        real_block = ocr._mrz_block_text
        calls: list[tuple[int, int]] = []

        def counting_block(region: Image.Image) -> str:
            calls.append(region.size)
            return real_block(region)

        page = mrz_pages.passport_page()
        with mock.patch.object(ocr, "_mrz_block_text", counting_block):
            upright = _read(page)
        self.assertEqual(upright.mrz_rotation, 0)
        self.assertLessEqual(len(calls), 1)

        calls.clear()
        with mock.patch.object(ocr, "_mrz_block_text", counting_block):
            upside_down = _read(mrz_pages.rotated(page, 180))
        self.assertEqual(upside_down.mrz_rotation, 180)
        self.assertGreater(len(calls), 1)


class StorageTests(unittest.TestCase):
    def test_uploaded_bytes_are_stored_unchanged(self) -> None:
        sideways = mrz_pages.rotated(mrz_pages.passport_page(), 90)
        payload = mrz_pages.png_bytes(sideways)
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)

        with mock.patch.multiple(
            db,
            DATA_DIR=root,
            DB_PATH=root / "app.db",
            UPLOADS_DIR=root / "uploads",
        ):
            db.init_db()
            candidate_id = db.create_candidate({}, {}, statuses.DRAFT)
            db.add_document(
                candidate_id, "passport", "Passport", "photo.png", payload
            )
            stored = db.list_documents(candidate_id)[0]
            saved = db.document_bytes(stored)

        self.assertEqual(saved, payload)
        assert saved is not None
        with Image.open(io.BytesIO(saved)) as image:
            self.assertEqual(image.size, sideways.size)


if __name__ == "__main__":
    unittest.main()
