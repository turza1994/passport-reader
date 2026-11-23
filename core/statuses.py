"""Candidate status rules.

Edit ``REQUIRED_FIELDS`` or ``COMPLETED_ENABLED`` to change the rules; no other
module needs to change.
"""

from __future__ import annotations

from typing import Literal

BadgeColor = Literal["red", "orange", "yellow", "blue", "green", "violet", "gray"]

DRAFT = "Draft"
NEEDS_REVIEW = "Needs Review"
VERIFIED = "Verified"
COMPLETED = "Completed"

ALL_STATUSES: tuple[str, ...] = (DRAFT, NEEDS_REVIEW, VERIFIED, COMPLETED)

# Completed only makes sense once medical portal registration is implemented.
COMPLETED_ENABLED = False

# Fields an employee must confirm before a candidate can be Verified.
# Supporting documents must never be required here, so a candidate that is
# missing one can still be saved and reviewed.
REQUIRED_FIELDS: tuple[str, ...] = (
    "surname",
    "given_names",
    "date_of_birth",
    "nationality",
    "passport_number",
    "expiry_date",
)

FIELD_LABELS: dict[str, str] = {
    "surname": "Surname",
    "given_names": "Given names",
    "sex": "Sex",
    "date_of_birth": "Date of birth",
    "nationality": "Nationality",
    "passport_number": "Passport number",
    "passport_country": "Passport country",
    "issue_date": "Issue date",
    "expiry_date": "Expiry date",
    "place_of_birth": "Place of birth",
    "issuing_authority": "Issuing authority",
    "notes": "Notes",
}

SOURCE_LABELS: dict[str, str] = {
    "mrz": "Read from MRZ",
    "visual": "Read from printed page (check)",
    "manual": "Entered manually",
    "": "Not detected - enter manually",
}


def missing_required(values: dict[str, str]) -> list[str]:
    """Required fields that are still empty for ``values``."""
    return [name for name in REQUIRED_FIELDS if not values.get(name, "").strip()]


def status_for(values: dict[str, str], verified: bool) -> str:
    """Status implied by the data and the employee's verification."""
    if missing_required(values):
        return DRAFT
    return VERIFIED if verified else NEEDS_REVIEW


def selectable_statuses() -> tuple[str, ...]:
    """Statuses this build can show."""
    if COMPLETED_ENABLED:
        return ALL_STATUSES
    return tuple(status for status in ALL_STATUSES if status != COMPLETED)


def badge_color(status: str) -> BadgeColor:
    colors: dict[str, BadgeColor] = {
        DRAFT: "gray",
        NEEDS_REVIEW: "orange",
        VERIFIED: "green",
        COMPLETED: "blue",
    }
    return colors.get(status, "gray")
