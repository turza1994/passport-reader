"""Intake: passport first, supporting documents optional."""

from __future__ import annotations

from typing import Any

import streamlit as st

from app_pages import _fields
from core import db, ocr, passport, statuses

DOCUMENT_TYPES = ["pdf", "png", "jpg", "jpeg", "webp", "tif", "tiff"]


def _read_passport(uploaded: Any) -> dict[str, Any]:
    content = uploaded.getvalue()
    text = ""
    mrz_text = ""
    method = ""
    error = ""
    try:
        result = ocr.read_document(uploaded.name, content)
        text = result.text
        mrz_text = result.mrz_text
        method = result.method
    except ocr.OcrError as exc:
        error = str(exc)
    return {
        "filename": uploaded.name,
        "data": content,
        "text": text,
        "method": method,
        "error": error,
        "extraction": passport.extract_passport(text, mrz_text),
    }


def _merge_sources(
    values: dict[str, str], extraction: passport.Extraction
) -> dict[str, str]:
    sources: dict[str, str] = {}
    for name in db.CANDIDATE_FIELDS:
        detected = extraction.values.get(name, "")
        if values[name] != detected:
            sources[name] = "manual" if values[name] else ""
        elif detected:
            sources[name] = extraction.sources.get(name, "")
        else:
            sources[name] = ""
    return sources


def _save_supporting_document(
    candidate_id: int, uploaded: Any, label: str
) -> None:
    content = uploaded.getvalue()
    text = ""
    state = ""
    try:
        result = ocr.read_document(uploaded.name, content)
        text = result.text
        state = f"OCR: {result.method}"
    except ocr.OcrError as exc:
        state = f"OCR failed: {exc}"
    db.add_document(
        candidate_id,
        "supporting",
        label.strip() or "Supporting document",
        uploaded.name,
        content,
        text,
        state,
    )


st.title("New candidate")
st.caption(
    "A passport is required. A supporting document is optional now and can be "
    "added later."
)

if error := st.session_state.pop("intake_error", ""):
    st.error(error)

with st.form("read_passport"):
    passport_file = st.file_uploader(
        "Passport scan",
        type=DOCUMENT_TYPES,
        accept_multiple_files=False,
        help=(
            "Image or PDF. The machine-readable zone is read automatically, "
            "including scans that are upright, sideways or upside-down."
        ),
    )
    read = st.form_submit_button(
        "Read passport", icon=":material/document_scanner:", width="stretch"
    )

if read:
    if passport_file is None:
        st.error("Add the passport scan before reading it.")
    else:
        st.session_state["intake"] = _read_passport(passport_file)

intake = st.session_state.get("intake")
if intake is None:
    st.stop()

extraction: passport.Extraction = intake["extraction"]
st.divider()

if intake["error"]:
    st.warning(
        f"{intake['error']} You can still enter the details manually and save "
        "the candidate.",
        title="The passport could not be read automatically",
    )
else:
    st.caption(f"Read from {intake['filename']} ({intake['method']}).")

if extraction.notes:
    st.info("\n\n".join(f"- {note}" for note in extraction.notes))

if "mrz" in {
    extraction.sources.get("surname", ""),
    extraction.sources.get("given_names", ""),
}:
    st.warning(
        "The name is read from the machine-readable zone, which has no check "
        "digit for the name. Check it against the printed page before you "
        "verify this candidate.",
        title="Verify the name yourself",
    )

with st.form("save_candidate"):
    st.subheader("Check the details")
    st.caption(
        "Anything not detected stays empty. Fill it in manually only if the "
        "document shows it."
    )
    edited: dict[str, str] = {}
    field_columns = st.columns(2)
    position = 0
    for name in db.CANDIDATE_FIELDS:
        if name == "notes":
            continue
        with field_columns[position % 2]:
            edited[name] = _fields.render_field(
                name,
                extraction.values.get(name, ""),
                extraction.sources.get(name, ""),
                key_prefix="intake",
            )
        position += 1
    edited["notes"] = _fields.render_field(
        "notes", "", "", key_prefix="intake"
    )

    st.divider()
    st.subheader("Supporting document (optional)")
    supporting_file = st.file_uploader(
        "Supporting document",
        type=DOCUMENT_TYPES,
        accept_multiple_files=False,
        key="intake_supporting_file",
    )
    supporting_label = st.text_input(
        "Document label",
        placeholder="e.g. Medical certificate",
        key="intake_supporting_label",
    )
    saved = st.form_submit_button(
        "Save candidate", icon=":material/save:", width="stretch"
    )

if saved:
    cleaned = {
        name: (edited.get(name) or "").strip() for name in db.CANDIDATE_FIELDS
    }
    candidate_id = db.create_candidate(
        cleaned,
        _merge_sources(cleaned, extraction),
        statuses.status_for(cleaned, verified=False),
    )
    document_state = (
        f"OCR: {intake['method']}"
        if intake["method"]
        else (intake["error"] or "Not read")
    )
    db.add_document(
        candidate_id,
        "passport",
        "Passport",
        intake["filename"],
        intake["data"],
        intake["text"],
        document_state,
    )
    if supporting_file is not None:
        _save_supporting_document(candidate_id, supporting_file, supporting_label)

    st.session_state.pop("intake", None)
    st.session_state["open_candidate_id"] = candidate_id
    st.session_state["flash"] = (
        "Candidate saved. "
        + (
            "A supporting document can be added later."
            if supporting_file is None
            else "Supporting document stored."
        )
    )
    st.switch_page("app_pages/candidates.py")
