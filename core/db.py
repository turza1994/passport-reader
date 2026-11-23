"""SQLite persistence for candidates and their uploaded documents."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "app.db"
UPLOADS_DIR = DATA_DIR / "uploads"

CANDIDATE_FIELDS: tuple[str, ...] = (
    "surname",
    "given_names",
    "sex",
    "date_of_birth",
    "nationality",
    "passport_number",
    "passport_country",
    "issue_date",
    "expiry_date",
    "place_of_birth",
    "issuing_authority",
    "notes",
)

_CANDIDATE_COLUMNS = ",\n    ".join(
    f"{name} TEXT NOT NULL DEFAULT ''" for name in CANDIDATE_FIELDS
)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    verified_at TEXT NOT NULL DEFAULT '',
    field_sources TEXT NOT NULL DEFAULT '{{}}',
    {_CANDIDATE_COLUMNS}
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    original_filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    ocr_text TEXT NOT NULL DEFAULT '',
    extraction_status TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_documents_candidate ON documents(candidate_id);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def _connect() -> Generator[sqlite3.Connection, None, None]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    with _connect() as connection:
        connection.executescript(_SCHEMA)


def _row_to_candidate(row: sqlite3.Row) -> dict[str, Any]:
    candidate = dict(row)
    try:
        candidate["field_sources"] = json.loads(candidate.get("field_sources") or "{}")
    except json.JSONDecodeError:
        candidate["field_sources"] = {}
    return candidate


def create_candidate(
    values: dict[str, str],
    field_sources: dict[str, str],
    status: str,
    verified_at: str = "",
) -> int:
    now = _now()
    columns = ", ".join(CANDIDATE_FIELDS)
    placeholders = ", ".join("?" for _ in CANDIDATE_FIELDS)
    with _connect() as connection:
        cursor = connection.execute(
            f"INSERT INTO candidates "
            f"(created_at, updated_at, status, verified_at, field_sources, {columns}) "
            f"VALUES (?, ?, ?, ?, ?, {placeholders})",
            (
                now,
                now,
                status,
                verified_at,
                json.dumps(field_sources),
                *(values.get(name, "") for name in CANDIDATE_FIELDS),
            ),
        )
        return int(cursor.lastrowid or 0)


def update_candidate(
    candidate_id: int,
    values: dict[str, str],
    field_sources: dict[str, str],
    status: str,
    verified_at: str = "",
) -> None:
    assignments = ", ".join(f"{name} = ?" for name in CANDIDATE_FIELDS)
    with _connect() as connection:
        connection.execute(
            f"UPDATE candidates SET updated_at = ?, status = ?, verified_at = ?, "
            f"field_sources = ?, {assignments} WHERE id = ?",
            (
                _now(),
                status,
                verified_at,
                json.dumps(field_sources),
                *(values.get(name, "") for name in CANDIDATE_FIELDS),
                candidate_id,
            ),
        )


def get_candidate(candidate_id: int) -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
    return _row_to_candidate(row) if row else None


def list_candidates(search: str = "", status: str = "") -> list[dict[str, Any]]:
    query = "SELECT * FROM candidates"
    conditions: list[str] = []
    parameters: list[Any] = []
    if search.strip():
        needle = f"%{search.strip()}%"
        conditions.append(
            "(surname LIKE ? OR given_names LIKE ? OR passport_number LIKE ?)"
        )
        parameters.extend([needle, needle, needle])
    if status:
        conditions.append("status = ?")
        parameters.append(status)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY updated_at DESC, id DESC"
    with _connect() as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [_row_to_candidate(row) for row in rows]


def add_document(
    candidate_id: int,
    kind: str,
    label: str,
    original_filename: str,
    content: bytes,
    ocr_text: str = "",
    extraction_status: str = "",
) -> int:
    """Store the file on disk and record it. Files are written once, here."""
    target_dir = UPLOADS_DIR / str(candidate_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(original_filename).suffix.lower()
    stored_path = target_dir / f"{uuid.uuid4().hex}{suffix}"
    stored_path.write_bytes(content)
    with _connect() as connection:
        cursor = connection.execute(
            "INSERT INTO documents "
            "(candidate_id, kind, label, original_filename, stored_path, "
            "uploaded_at, ocr_text, extraction_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                candidate_id,
                kind,
                label,
                original_filename,
                str(stored_path),
                _now(),
                ocr_text,
                extraction_status,
            ),
        )
        return int(cursor.lastrowid or 0)


def list_documents(candidate_id: int, kind: str = "") -> list[dict[str, Any]]:
    query = "SELECT * FROM documents WHERE candidate_id = ?"
    parameters: list[Any] = [candidate_id]
    if kind:
        query += " AND kind = ?"
        parameters.append(kind)
    query += " ORDER BY uploaded_at ASC, id ASC"
    with _connect() as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]


def document_bytes(document: dict[str, Any]) -> bytes | None:
    path = Path(document.get("stored_path", ""))
    try:
        return path.read_bytes()
    except OSError:
        return None
