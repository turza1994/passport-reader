"""Passport field extraction.

The machine-readable zone (TD3, two 44-character lines) is the only source that
is trusted automatically, and only when its check digits validate. Everything
else is either read from an explicit label on the printed page (flagged for
checking) or left empty for manual entry. Nothing is ever guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from mrz.checker.td3 import TD3CodeChecker

TD3_LINE_LENGTH = 44
NAME_FIELD_START = 5
MIN_FILLER_RUN = 3
MAX_COMPACT_OFFSETS = 240
MRZ_LINE_PATTERN = re.compile(r"[A-Z0-9<]{40,46}")
NON_MRZ_CHARS = re.compile(r"[^A-Z0-9<]")
WHITESPACE = re.compile(r"\s+")

_MONTH_NAMES = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

_NUMERIC_DATE = re.compile(r"(?P<day>\d{1,2})[./\- ](?P<month>\d{1,2})[./\- ](?P<year>\d{4})")
_WORDED_DATE = re.compile(
    r"(?P<day>\d{1,2})[./\- ]?(?P<month>[A-Z]{3,9})[./\- ](?P<year>\d{4})"
)
_PLAIN_VALUE = re.compile(r"[A-Za-z][A-Za-z .,'\-]{1,39}")

VISUAL_LABELS: dict[str, tuple[str, ...]] = {
    "place_of_birth": ("place of birth", "birth place", "birthplace"),
    "issue_date": ("date of issue", "issue date"),
    "issuing_authority": ("issuing authority", "authority", "issued by"),
}


@dataclass
class Extraction:
    """Passport values found in a document, with where each came from."""

    values: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    mrz_lines: list[str] = field(default_factory=list)
    mrz_original_lines: list[str] = field(default_factory=list)
    name_repaired: bool = False


def extract_passport(text: str, mrz_text: str = "") -> Extraction:
    """Extract passport fields from page text and a dedicated MRZ OCR pass."""
    extraction = Extraction()
    candidates = mrz_candidates(mrz_text) + mrz_candidates(text)
    found = _first_valid_checker(candidates)

    if found is None:
        if candidates:
            extraction.notes.append(
                "A travel-document code was found but its check digits did not "
                "validate, so no fields were filled. Enter the details manually."
            )
        else:
            extraction.notes.append(
                "No machine-readable zone was detected. Enter the passport details manually."
            )
    else:
        checker, original = found
        extraction.mrz_lines = checker.mrz_code.splitlines()
        _apply_mrz(extraction, checker)
        extraction.notes.append(
            "Machine-readable zone validated. Printed-page values were not needed."
        )
        if original is not None:
            extraction.mrz_original_lines = original.splitlines()
            extraction.name_repaired = True
            extraction.notes.append(
                "The name line's filler characters were unclear in the photo and "
                "were treated as padding. The original reading was kept; please "
                "check the name."
            )

    for name, labels in VISUAL_LABELS.items():
        if extraction.values.get(name):
            continue
        parser = _read_visual_date if name == "issue_date" else _read_visual_text
        value = _visual_value(text, labels, parser)
        if value:
            extraction.values[name] = value
            extraction.sources[name] = "visual"
            extraction.notes.append(
                f"{_pretty(name)} was read from the printed page; please check it."
            )

    return extraction


def _apply_mrz(extraction: Extraction, checker: TD3CodeChecker) -> None:
    fields = checker.fields()
    detected = {
        "surname": _clean(fields.surname),
        "given_names": _clean(fields.name),
        "sex": _clean_sex(fields.sex),
        "date_of_birth": _mrz_date(fields.birth_date, is_birth=True),
        "nationality": _clean(fields.nationality),
        "passport_number": _clean(fields.document_number),
        "passport_country": _clean(fields.country),
        "expiry_date": _mrz_date(fields.expiry_date, is_birth=False),
    }
    for name, value in detected.items():
        if value:
            extraction.values[name] = value
            extraction.sources[name] = "mrz"


def mrz_candidates(text: str) -> list[str]:
    """Plausible TD3 line pairs found in OCR text, longest lines first."""
    if not text:
        return []
    candidates: list[str] = []
    lines = [WHITESPACE.sub("", line.upper()) for line in text.splitlines()]
    lines = [line for line in lines if MRZ_LINE_PATTERN.fullmatch(line)]
    for first, second in zip(lines, lines[1:]):
        if len(first) >= TD3_LINE_LENGTH and len(second) >= TD3_LINE_LENGTH:
            candidates.append(
                first[:TD3_LINE_LENGTH] + "\n" + second[:TD3_LINE_LENGTH]
            )
        elif len(second) >= TD3_LINE_LENGTH and len(first) >= TD3_LINE_LENGTH - 4:
            # Short name lines are completed with the MRZ padding character only.
            padded = first.ljust(TD3_LINE_LENGTH, "<")
            candidates.append(padded + "\n" + second[:TD3_LINE_LENGTH])

    compact = NON_MRZ_CHARS.sub("", text.upper())
    limit = min(MAX_COMPACT_OFFSETS, max(0, len(compact) - 87))
    for offset in range(limit):
        chunk = compact[offset : offset + 88]
        if chunk[0] != "P":
            continue
        candidates.append(chunk[:TD3_LINE_LENGTH] + "\n" + chunk[TD3_LINE_LENGTH:])
    return candidates


def valid_mrz(code: str) -> bool:
    """True only for a TD3 pair whose check digits validate."""
    return _first_valid_checker([code]) is not None


def _first_valid_checker(candidates: list[str]) -> tuple[TD3CodeChecker, str | None] | None:
    """First valid candidate, with the original text when the name line was repaired.

    Untouched readings are always preferred, so a cleanly read MRZ is never
    altered. If no candidate validates as read, the name line's filler is
    repaired and the check digits are required to pass again.
    """
    for code in candidates:
        checker = _validate(code)
        if checker is not None:
            return checker, None
    for code in candidates:
        repaired = _repair_name_line(code)
        if repaired == code:
            continue
        checker = _validate(repaired)
        if checker is not None:
            return checker, code
    return None


def _validate(code: str) -> TD3CodeChecker | None:
    try:
        checker = TD3CodeChecker(code, check_expiry=False)
    except Exception:  # noqa: BLE001 - malformed candidates are simply skipped
        return None
    return checker if checker.result else None


def _repair_name_line(code: str) -> str:
    """Rewrite impossible characters after the name line's padding as filler.

    In a TD3 name line a single '<' separates name components and '<<' separates
    the surname from the given names, so a run of three or more is always
    padding and everything after it must be '<'. Photos often make OCR read
    those chevrons as letters or digits, which fails the format check even when
    every check digit is correct. Only the filler is rewritten; the document
    type, the country and the name itself are never touched.
    """
    lines = code.splitlines()
    if len(lines) != 2 or any(len(line) != TD3_LINE_LENGTH for line in lines):
        return code
    name_line = lines[0]
    filler = "<" * MIN_FILLER_RUN
    for index in range(NAME_FIELD_START, TD3_LINE_LENGTH - MIN_FILLER_RUN + 1):
        if name_line[index : index + MIN_FILLER_RUN] != filler:
            continue
        repaired = name_line[:index] + "<" * (TD3_LINE_LENGTH - index)
        return "\n".join([repaired, lines[1]])
    return code


def _clean(value: str) -> str:
    return WHITESPACE.sub(" ", (value or "").replace("<", " ")).strip()


def _clean_sex(value: str) -> str:
    cleaned = (value or "").strip().upper()
    return cleaned if cleaned in {"M", "F"} else ""


def _mrz_date(raw: str, *, is_birth: bool) -> str:
    if not re.fullmatch(r"\d{6}", raw or ""):
        return ""
    year_short, month, day = int(raw[0:2]), int(raw[2:4]), int(raw[4:6])
    year = 2000 + year_short
    today = date.today()
    if is_birth:
        if year > today.year:
            year = 1900 + year_short
    elif year > today.year + 20 or year < today.year - 30:
        year = 1900 + year_short
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def _visual_value(text: str, labels: tuple[str, ...], parser) -> str:
    for line in (text or "").splitlines():
        lowered = line.lower()
        for label in labels:
            position = lowered.find(label)
            if position == -1:
                continue
            remainder = line[position + len(label) :].strip(" :.-|")
            value = parser(remainder)
            if value:
                return value
    return ""


def _read_visual_text(remainder: str) -> str:
    candidate = WHITESPACE.sub(" ", remainder).strip(" .,:;-")
    if not _PLAIN_VALUE.fullmatch(candidate):
        return ""
    if candidate.lower() in {"date of issue", "issue date"}:
        return ""
    return candidate


def _read_visual_date(remainder: str) -> str:
    upper = remainder.upper()
    match = _NUMERIC_DATE.search(upper)
    if match:
        return _iso_date(
            int(match["year"]), int(match["month"]), int(match["day"])
        )
    worded = _WORDED_DATE.search(upper)
    if worded:
        month = _MONTH_NAMES.get(worded["month"])
        if month:
            return _iso_date(int(worded["year"]), month, int(worded["day"]))
    return ""


def _iso_date(year: int, month: int, day: int) -> str:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def _pretty(name: str) -> str:
    return name.replace("_", " ").capitalize()
