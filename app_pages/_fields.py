"""Shared input rendering for candidate fields."""

from __future__ import annotations

from datetime import date

import streamlit as st

from core.statuses import FIELD_LABELS, SOURCE_LABELS

DATE_RANGES: dict[str, tuple[date, date]] = {
    "date_of_birth": (date(1900, 1, 1), date.today()),
    "issue_date": (date(1950, 1, 1), date(2100, 12, 31)),
    "expiry_date": (date(1950, 1, 1), date(2100, 12, 31)),
}
SEX_OPTIONS: tuple[str, ...] = ("", "M", "F", "X")


def render_field(field: str, value: str, source: str, *, key_prefix: str) -> str:
    """Render one editable candidate field and return its current value."""
    label = FIELD_LABELS.get(field, field)
    key = f"{key_prefix}_{field}"
    output = ""

    if field in DATE_RANGES:
        minimum, maximum = DATE_RANGES[field]
        result = st.date_input(
            label,
            value=_parse_date(value),
            min_value=minimum,
            max_value=maximum,
            format="DD/MM/YYYY",
            key=key,
        )
        output = result.isoformat() if isinstance(result, date) else ""
    elif field == "sex":
        options = list(SEX_OPTIONS)
        index = options.index(value) if value in options else 0
        output = st.selectbox(
            label,
            options,
            index=index,
            format_func=lambda option: option or "Not recorded",
            key=key,
        )
    elif field == "notes":
        output = st.text_area(label, value=value, height=100, key=key)
    else:
        output = st.text_input(label, value=value, key=key)

    st.caption(SOURCE_LABELS.get(source, SOURCE_LABELS[""]))
    return output


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
