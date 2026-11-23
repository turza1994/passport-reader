"""MRZ parsing and check-digit rules, without running Tesseract."""

from __future__ import annotations

import unittest

from core import passport

from tests.mrz_pages import (
    BAD_CHECK_DIGIT_LINE_2,
    SPECIMEN_LINE_1,
    SPECIMEN_LINE_2,
)

SPECIMEN = f"{SPECIMEN_LINE_1}\n{SPECIMEN_LINE_2}"
BAD_CHECK_DIGIT = f"{SPECIMEN_LINE_1}\n{BAD_CHECK_DIGIT_LINE_2}"
# The name ends after "ANNA<MARIA" (25 characters), then the padding that a
# photo makes OCR read as letters. This is the failure seen on real photos.
NAME_END = 25
MISTREAD_FILLER_LINE_1 = SPECIMEN_LINE_1[:NAME_END] + "<" * 10 + "KKKKKKEKS"
MISTREAD_FILLER = f"{MISTREAD_FILLER_LINE_1}\n{SPECIMEN_LINE_2}"
SHORT_FILLER_LINE_1 = SPECIMEN_LINE_1[:NAME_END] + "<<" + "K" * 17


class CheckDigitTests(unittest.TestCase):
    def test_specimen_validates(self) -> None:
        self.assertTrue(passport.valid_mrz(SPECIMEN))

    def test_corrupted_check_digit_is_rejected(self) -> None:
        self.assertFalse(passport.valid_mrz(BAD_CHECK_DIGIT))

    def test_short_name_line_is_padded(self) -> None:
        candidates = passport.mrz_candidates(f"{SPECIMEN_LINE_1[:-1]}\n{SPECIMEN_LINE_2}")
        self.assertTrue(any(passport.valid_mrz(code) for code in candidates))

    def test_single_line_scan_is_split(self) -> None:
        candidates = passport.mrz_candidates(SPECIMEN_LINE_1 + SPECIMEN_LINE_2)
        self.assertTrue(any(passport.valid_mrz(code) for code in candidates))


class ExtractionTests(unittest.TestCase):
    def test_mrz_fills_passport_fields(self) -> None:
        extraction = passport.extract_passport("", SPECIMEN)
        self.assertEqual(extraction.values["surname"], "ERIKSSON")
        self.assertEqual(extraction.values["given_names"], "ANNA MARIA")
        self.assertEqual(extraction.values["passport_number"], "L898902C3")
        self.assertEqual(extraction.values["nationality"], "UTO")
        self.assertEqual(extraction.values["passport_country"], "UTO")
        self.assertEqual(extraction.values["date_of_birth"], "1974-08-12")
        self.assertEqual(extraction.values["expiry_date"], "2012-04-15")
        self.assertEqual(extraction.values["sex"], "F")
        self.assertEqual(extraction.sources["surname"], "mrz")
        self.assertEqual(len(extraction.mrz_lines), 2)

    def test_bad_check_digit_fills_nothing(self) -> None:
        extraction = passport.extract_passport("", BAD_CHECK_DIGIT)
        self.assertFalse({k: v for k, v in extraction.values.items() if v})
        self.assertTrue(
            any("check digits did not validate" in note for note in extraction.notes)
        )

    def test_clean_mrz_needs_no_repair(self) -> None:
        extraction = passport.extract_passport("", SPECIMEN)
        self.assertFalse(extraction.name_repaired)
        self.assertFalse(extraction.mrz_original_lines)
        self.assertFalse(any("filler" in note for note in extraction.notes))

    def test_missing_mrz_fills_nothing(self) -> None:
        extraction = passport.extract_passport("no document text here", "")
        self.assertFalse({k: v for k, v in extraction.values.items() if v})
        self.assertTrue(
            any("No machine-readable zone" in note for note in extraction.notes)
        )


class NameLineRepairTests(unittest.TestCase):
    def test_misread_filler_is_repaired(self) -> None:
        extraction = passport.extract_passport("", MISTREAD_FILLER)
        self.assertEqual(extraction.values["surname"], "ERIKSSON")
        self.assertEqual(extraction.values["given_names"], "ANNA MARIA")
        self.assertTrue(extraction.name_repaired)
        self.assertEqual(extraction.mrz_lines[0], SPECIMEN_LINE_1)
        self.assertEqual(extraction.mrz_original_lines[0], MISTREAD_FILLER_LINE_1)
        self.assertTrue(any("filler" in note for note in extraction.notes))

    def test_repair_still_requires_valid_check_digits(self) -> None:
        code = f"{MISTREAD_FILLER_LINE_1}\n{BAD_CHECK_DIGIT_LINE_2}"
        self.assertFalse(passport.valid_mrz(code))
        extraction = passport.extract_passport("", code)
        self.assertFalse({k: v for k, v in extraction.values.items() if v})
        self.assertFalse(extraction.name_repaired)

    def test_short_filler_run_is_not_repaired(self) -> None:
        # Two '<' also separate the surname from the given names, so they are
        # not proof of padding and must not be rewritten.
        code = f"{SHORT_FILLER_LINE_1}\n{SPECIMEN_LINE_2}"
        self.assertFalse(passport.valid_mrz(code))
        self.assertFalse(
            {k: v for k, v in passport.extract_passport("", code).values.items() if v}
        )

    def test_wrong_length_name_line_is_not_repaired(self) -> None:
        for length in (43, 45):
            with self.subTest(length=length):
                name_line = (SPECIMEN_LINE_1 + "<")[:length]
                self.assertFalse(
                    passport.valid_mrz(f"{name_line}\n{SPECIMEN_LINE_2}")
                )


if __name__ == "__main__":
    unittest.main()
