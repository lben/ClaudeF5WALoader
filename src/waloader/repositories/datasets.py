from __future__ import annotations

import json
import sqlite3

from waloader.models import DatasetConcept, DatasetFile
from waloader.util import utc_now_iso


def create_concept(conn: sqlite3.Connection, app_id: int, name: str) -> DatasetConcept:
    cur = conn.execute(
        "INSERT INTO dataset_concepts (app_id, name, created_at) VALUES (?,?,?)",
        (app_id, name, utc_now_iso()),
    )
    return get_concept(conn, cur.lastrowid)


def get_concept(conn: sqlite3.Connection, concept_id: int) -> DatasetConcept:
    row = conn.execute("SELECT * FROM dataset_concepts WHERE id=?", (concept_id,)).fetchone()
    if row is None:
        raise KeyError(f"No dataset concept with id {concept_id}")
    return DatasetConcept.from_row(row)


def get_concept_by_name(conn: sqlite3.Connection, app_id: int, name: str) -> DatasetConcept | None:
    row = conn.execute(
        "SELECT * FROM dataset_concepts WHERE app_id=? AND name=?", (app_id, name)
    ).fetchone()
    return DatasetConcept.from_row(row) if row else None


def list_concepts(conn: sqlite3.Connection, app_id: int) -> list[DatasetConcept]:
    rows = conn.execute(
        "SELECT * FROM dataset_concepts WHERE app_id=? ORDER BY name", (app_id,)
    ).fetchall()
    return [DatasetConcept.from_row(r) for r in rows]


def delete_concept(conn: sqlite3.Connection, concept_id: int) -> None:
    conn.execute("DELETE FROM dataset_concepts WHERE id=?", (concept_id,))


def add_file(
    conn: sqlite3.Connection,
    *,
    concept_id: int,
    original_filename: str,
    original_path: str,
    canonical_path: str,
    sheet_name: str | None,
    schema: dict[str, str],
    size_bytes: int,
    uploaded_by: int | None,
) -> DatasetFile:
    conn.execute("UPDATE dataset_files SET is_current=0 WHERE concept_id=?", (concept_id,))
    cur = conn.execute(
        """INSERT INTO dataset_files (concept_id, original_filename, original_path,
                                      canonical_path, sheet_name, schema_json, size_bytes,
                                      is_current, uploaded_by, uploaded_at)
           VALUES (?,?,?,?,?,?,?,1,?,?)""",
        (concept_id, original_filename, original_path, canonical_path, sheet_name,
         json.dumps(schema), size_bytes, uploaded_by, utc_now_iso()),
    )
    row = conn.execute("SELECT * FROM dataset_files WHERE id=?", (cur.lastrowid,)).fetchone()
    return DatasetFile.from_row(row)


def current_file(conn: sqlite3.Connection, concept_id: int) -> DatasetFile | None:
    row = conn.execute(
        "SELECT * FROM dataset_files WHERE concept_id=? AND is_current=1", (concept_id,)
    ).fetchone()
    return DatasetFile.from_row(row) if row else None


def file_history(conn: sqlite3.Connection, concept_id: int) -> list[DatasetFile]:
    rows = conn.execute(
        "SELECT * FROM dataset_files WHERE concept_id=? ORDER BY id DESC", (concept_id,)
    ).fetchall()
    return [DatasetFile.from_row(r) for r in rows]
