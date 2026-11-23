"""Candidate list and record review."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

from app_pages import _fields
from core import db, ocr, statuses

DOCUMENT_TYPES = ["pdf", "png", "jpg", "jpeg", "webp", "tif", "tiff"]


def _display_name(candidate: dict[str, Any]) -> str:
    name = f"{candidate.get('surname', '')} {candidate.get('given_names', '')}".strip()
    return name or f"Candidate {candidate.get('id', '')}"


def _selected_rows(event: Any) -> list[int]:
    if event is None:
        return []
    selection = getattr(event, "selection", None)
    if selection is None and isinstance(event, dict):
        selection = event.get("selection", {})
    rows = getattr(selection, "rows", None)
    if rows is None and isinstance(selection, dict):
        rows = selection.get("rows", [])
    return list(rows or [])


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


def _render_documents(
    candidate: dict[str, Any], documents: list[dict[str, Any]]
) -> None:
    candidate_id = int(candidate["id"])
    passport_documents = [doc for doc in documents if doc["kind"] == "passport"]
    supporting_documents = [doc for doc in documents if doc["kind"] == "supporting"]
    name = _display_name(candidate)

    st.subheader("Documents")
    for document in passport_documents:
        content = db.document_bytes(document)
        st.caption(
            f"Passport - {document['original_filename']} "
            f"(added {document['uploaded_at'].replace('T', ' ')})"
        )
        if content is None:
            st.warning("The passport file could not be found on disk.", title="Missing file")
        elif Path(document["original_filename"]).suffix.lower() == ".pdf":
            st.pdf(content, height=380, alt=f"Passport scan for {name}")
        else:
            st.image(content, alt=f"Passport scan for {name}")

    st.subheader("Supporting documents")
    if not supporting_documents:
        st.caption(
            "None yet. Add one below, or leave it pending and enter any missing "
            "details manually."
        )
    for document in supporting_documents:
        label = document["label"] or "Supporting document"
        with st.expander(f"{label} - {document['original_filename']}"):
            st.caption(
                f"Added {document['uploaded_at'].replace('T', ' ')} "
                f"- {document['extraction_status'] or 'No text read'}"
            )
            if document["ocr_text"]:
                st.text(document["ocr_text"][:4000])
            else:
                st.caption("No text was read from this file.")

    with st.form(f"add_supporting_{candidate_id}", clear_on_submit=True):
        upload = st.file_uploader("Supporting document", type=DOCUMENT_TYPES)
        label = st.text_input(
            "Document label", placeholder="e.g. Medical certificate"
        )
        added = st.form_submit_button(
            "Add document", icon=":material/upload_file:", width="stretch"
        )
    if added:
        if upload is None:
            st.error("Choose a file to add.")
        else:
            _save_supporting_document(candidate_id, upload, label)
            st.session_state["flash"] = "Supporting document added."
            st.rerun()


def _render_candidate(candidate_id: int) -> None:
    candidate = db.get_candidate(candidate_id)
    if candidate is None:
        st.warning("This candidate could not be found.")
        return

    values = {name: candidate.get(name, "") for name in db.CANDIDATE_FIELDS}
    sources: dict[str, str] = candidate["field_sources"]
    documents = db.list_documents(candidate_id)
    missing = statuses.missing_required(values)
    verified_at = candidate["verified_at"]

    st.divider()
    with st.container(horizontal=True, vertical_alignment="center"):
        st.subheader(_display_name(candidate))
        st.badge(candidate["status"], color=statuses.badge_color(candidate["status"]))
        if not any(doc["kind"] == "supporting" for doc in documents):
            st.badge("Supporting document missing", color="gray")
        if st.button("Close record", icon=":material/close:", type="tertiary"):
            st.session_state.pop("active_candidate", None)
            st.rerun()

    if verified_at:
        st.caption(
            f"Verified on {verified_at.replace('T', ' ')}. "
            f"Created {candidate['created_at'].replace('T', ' ')}."
        )
    else:
        st.caption(f"Created {candidate['created_at'].replace('T', ' ')}.")

    if missing:
        st.warning(
            "Required fields still empty: "
            + ", ".join(statuses.FIELD_LABELS[name] for name in missing)
            + ". Enter them from the document now, or save the candidate and "
            "complete them later.",
            icon=":material/pending_actions:",
            title="Waiting on passport details",
        )

    details_column, documents_column = st.columns([3, 2], gap="large")

    with details_column:
        st.subheader("Passport details")
        with st.form(f"edit_candidate_{candidate_id}"):
            edited: dict[str, str] = {}
            field_columns = st.columns(2)
            position = 0
            for name in db.CANDIDATE_FIELDS:
                if name == "notes":
                    continue
                with field_columns[position % 2]:
                    edited[name] = _fields.render_field(
                        name,
                        values[name],
                        sources.get(name, ""),
                        key_prefix=f"edit_{candidate_id}",
                    )
                position += 1
            edited["notes"] = _fields.render_field(
                "notes",
                values["notes"],
                sources.get("notes", ""),
                key_prefix=f"edit_{candidate_id}",
            )
            save = st.form_submit_button(
                "Save changes", icon=":material/save:", width="stretch"
            )

        if save:
            cleaned = {name: (edited.get(name) or "").strip() for name in db.CANDIDATE_FIELDS}
            updated_sources: dict[str, str] = {}
            for name in db.CANDIDATE_FIELDS:
                if cleaned[name] != values[name]:
                    updated_sources[name] = "manual" if cleaned[name] else ""
                else:
                    updated_sources[name] = sources.get(name, "")
            still_verified = bool(verified_at) and not statuses.missing_required(cleaned)
            new_status = statuses.status_for(cleaned, still_verified)
            db.update_candidate(
                candidate_id,
                cleaned,
                updated_sources,
                new_status,
                verified_at if still_verified else "",
            )
            st.session_state["flash"] = f"Changes saved. Status is now {new_status}."
            st.rerun()

        action_columns = st.columns(2)
        verify = action_columns[0].button(
            "Verify candidate",
            icon=":material/check_circle:",
            type="primary",
            width="stretch",
            disabled=bool(missing) or bool(verified_at),
        )
        reopen = action_columns[1].button(
            "Return to review",
            icon=":material/undo:",
            width="stretch",
            disabled=not verified_at,
        )
        st.caption("Verification uses the saved details, so save any edits first.")

        if verify:
            db.update_candidate(
                candidate_id,
                values,
                sources,
                statuses.VERIFIED,
                datetime.now().isoformat(timespec="seconds"),
            )
            st.session_state["flash"] = "Candidate verified."
            st.rerun()

        if reopen:
            new_status = statuses.status_for(values, False)
            db.update_candidate(candidate_id, values, sources, new_status, "")
            st.session_state["flash"] = f"Returned to review. Status is now {new_status}."
            st.rerun()

    with documents_column:
        _render_documents(candidate, documents)


st.title("Candidates")
st.caption(
    "Passport data, supporting documents and verification in one place. "
    "Supporting documents are optional."
)

if flash := st.session_state.pop("flash", ""):
    st.success(flash)

filter_columns = st.columns([3, 4])
search = filter_columns[0].text_input(
    "Search", placeholder="Name or passport number", key="candidate_search"
)
status_filter = filter_columns[1].segmented_control(
    "Status",
    ("All", *statuses.selectable_statuses()),
    default="All",
    required=True,
    key="status_filter",
)

rows = db.list_candidates(
    search=search,
    status="" if status_filter == "All" else status_filter,
)

if not rows:
    st.info(
        "No candidates match these filters."
        if search or status_filter != "All"
        else "No candidates yet. Add the first one from New candidate."
    )
    st.stop()

table: list[dict[str, Any]] = []
for row in rows:
    table.append(
        {
            "id": row["id"],
            "Candidate": _display_name(row),
            "Passport number": row["passport_number"] or "-",
            "Nationality": row["nationality"] or "-",
            "Date of birth": row["date_of_birth"] or "-",
            "Status": row["status"],
            "Last updated": row["updated_at"].replace("T", " "),
        }
    )

event = st.dataframe(
    table,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
    column_config={"id": None},
    key="candidate_table",
    alt=(
        "Candidates with passport number, nationality, date of birth and status. "
        "Select a row to open the record."
    ),
)

opened = st.session_state.pop("open_candidate_id", None)
if opened is not None:
    st.session_state["active_candidate"] = opened

selected = _selected_rows(event)
if selected and selected[0] < len(table):
    st.session_state["active_candidate"] = table[selected[0]]["id"]

candidate_id = st.session_state.get("active_candidate")
visible_ids = {row["id"] for row in rows}
if candidate_id is not None and candidate_id not in visible_ids:
    st.session_state.pop("active_candidate", None)
    candidate_id = None

if candidate_id is None:
    st.caption("Select a row to open the candidate record.")
else:
    _render_candidate(candidate_id)
