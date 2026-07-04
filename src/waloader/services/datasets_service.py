"""Dataset Concepts: named datasets apps load via waloader_sdk.datasets.

Storage model (G01 §3.5): every upload keeps the original file (timestamped,
under the concept directory) AND writes a canonical ``current.parquet``. The
SDK reads only the canonical Parquet, so child apps never need Excel readers.
Schema inference and diffing happen here, at upload time, using the declared
Excel sheet when relevant.
"""

from __future__ import annotations

import io
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from waloader.config import WALoaderConfig
from waloader.models import App, DatasetConcept, DatasetFile
from waloader.paths import ensure_dir
from waloader.repositories import audit as audit_repo
from waloader.repositories import datasets as datasets_repo
from waloader.services import layout
from waloader.util import utc_now

CONCEPT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
EXCEL_EXTENSIONS = {".xlsx", ".xls"}
CANONICAL_FILENAME = "current.parquet"


class DatasetError(Exception):
    """User-facing dataset problem; messages are safe to display."""


# --- concepts -------------------------------------------------------------


def create_concept(
    conn: sqlite3.Connection, app: App, name: str, *, actor: str = ""
) -> DatasetConcept:
    name = name.strip()
    if not CONCEPT_NAME_RE.match(name):
        raise DatasetError(
            "Concept names use lowercase letters, digits and underscores, starting "
            "with a letter (e.g. 'clients', 'reference_data')"
        )
    if datasets_repo.get_concept_by_name(conn, app.id, name) is not None:
        raise DatasetError(f"Concept '{name}' already exists for this app")
    concept = datasets_repo.create_concept(conn, app.id, name)
    audit_repo.record(conn, actor=actor, action="dataset.concept.create",
                      target=f"{app.slug}:{name}")
    conn.commit()
    return concept


def list_concepts_with_files(
    conn: sqlite3.Connection, app: App
) -> list[tuple[DatasetConcept, DatasetFile | None]]:
    return [
        (concept, datasets_repo.current_file(conn, concept.id))
        for concept in datasets_repo.list_concepts(conn, app.id)
    ]


def delete_concept(
    conn: sqlite3.Connection, config: WALoaderConfig, app: App, concept_id: int,
    *, actor: str = ""
) -> None:
    concept = datasets_repo.get_concept(conn, concept_id)
    datasets_repo.delete_concept(conn, concept_id)
    shutil.rmtree(layout.concept_dir(config, app.slug, concept.name), ignore_errors=True)
    audit_repo.record(conn, actor=actor, action="dataset.concept.delete",
                      target=f"{app.slug}:{concept.name}")
    conn.commit()


# --- reading + schema inference -------------------------------------------


def _require_sheet(extension: str, sheet_name: str | None) -> None:
    if extension in EXCEL_EXTENSIONS and not (sheet_name or "").strip():
        raise DatasetError(
            f"A sheet name is required for Excel ({extension}) uploads"
        )


def read_tabular(data: bytes, extension: str, sheet_name: str | None) -> pd.DataFrame:
    """Parse uploaded bytes into a DataFrame. Excel requires a sheet name."""
    extension = extension.lower()
    _require_sheet(extension, sheet_name)
    buffer = io.BytesIO(data)
    try:
        if extension == ".csv":
            return pd.read_csv(buffer)
        if extension == ".parquet":
            return pd.read_parquet(buffer)
        if extension in EXCEL_EXTENSIONS:
            try:
                return pd.read_excel(buffer, sheet_name=sheet_name)
            except ValueError as exc:
                if "Worksheet" in str(exc):
                    buffer.seek(0)
                    available = pd.ExcelFile(buffer).sheet_names
                    raise DatasetError(
                        f"Sheet {sheet_name!r} not found. Available sheets: {available}"
                    ) from exc
                raise
    except DatasetError:
        raise
    except Exception as exc:
        raise DatasetError(f"Could not read the {extension} file: {exc}") from exc
    raise DatasetError(f"Unsupported dataset file extension: {extension}")


def infer_schema(df: pd.DataFrame) -> dict[str, str]:
    """Column name -> pandas dtype string, in column order."""
    return {str(col): str(dtype) for col, dtype in df.dtypes.items()}


def inspect_upload(
    config: WALoaderConfig, filename: str, data: bytes, sheet_name: str | None
) -> tuple[dict[str, str], int]:
    """Validate + read an upload WITHOUT storing it. Returns (schema, row_count)."""
    extension = Path(filename).suffix.lower()
    allowed = [e.lower() for e in config.uploads.allowed_dataset_extensions]
    if extension not in allowed:
        raise DatasetError(
            f"File type {extension!r} is not allowed. Allowed: {allowed}"
        )
    limit = config.uploads.max_dataset_file_mb * 1024 * 1024
    if len(data) > limit:
        raise DatasetError(
            f"File is {len(data) / 1024 / 1024:.1f} MB; the limit is "
            f"{config.uploads.max_dataset_file_mb} MB"
        )
    df = read_tabular(data, extension, sheet_name)
    return infer_schema(df), len(df)


# --- schema diff ------------------------------------------------------------


@dataclass
class SchemaDiff:
    added: dict[str, str] = field(default_factory=dict)
    removed: dict[str, str] = field(default_factory=dict)
    changed: dict[str, tuple[str, str]] = field(default_factory=dict)  # col -> (old, new)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def format(self) -> str:
        """Copyable code-block text shown before a replacement is confirmed."""
        if not self.has_changes:
            return "No schema changes."
        lines = []
        for col, dtype in self.added.items():
            lines.append(f"+ added   {col} ({dtype})")
        for col, dtype in self.removed.items():
            lines.append(f"- removed {col} (was {dtype})")
        for col, (old, new) in self.changed.items():
            lines.append(f"~ changed {col}: {old} -> {new}")
        return "\n".join(lines)


def diff_schemas(old: dict[str, str], new: dict[str, str]) -> SchemaDiff:
    diff = SchemaDiff()
    for col, dtype in new.items():
        if col not in old:
            diff.added[col] = dtype
        elif old[col] != dtype:
            diff.changed[col] = (old[col], dtype)
    for col, dtype in old.items():
        if col not in new:
            diff.removed[col] = dtype
    return diff


def replacement_diff(
    conn: sqlite3.Connection,
    config: WALoaderConfig,
    concept: DatasetConcept,
    filename: str,
    data: bytes,
    sheet_name: str | None,
) -> SchemaDiff:
    """Diff an incoming replacement against the concept's current schema.

    Both sides use their own stored/declared Excel sheet where relevant.
    """
    current = datasets_repo.current_file(conn, concept.id)
    if current is None:
        return SchemaDiff()  # nothing to compare: first upload
    new_schema, _ = inspect_upload(config, filename, data, sheet_name)
    return diff_schemas(current.schema, new_schema)


# --- storing uploads ---------------------------------------------------------


def store_upload(
    conn: sqlite3.Connection,
    config: WALoaderConfig,
    app: App,
    concept: DatasetConcept,
    *,
    filename: str,
    data: bytes,
    sheet_name: str | None = None,
    uploaded_by: int | None = None,
    actor: str = "",
) -> DatasetFile:
    """Persist an upload: original file + canonical parquet + DB metadata.

    For replacements the UI must run ``replacement_diff`` first and get user
    confirmation when it reports changes; this function just stores.
    """
    extension = Path(filename).suffix.lower()
    schema, _rows = inspect_upload(config, filename, data, sheet_name)
    df = read_tabular(data, extension, sheet_name)

    concept_path = ensure_dir(layout.concept_dir(config, app.slug, concept.name))
    stamp = utc_now().strftime("%Y%m%dT%H%M%S")
    safe_name = re.sub(r"[^A-Za-z0-9._\-]", "_", filename)
    original_path = concept_path / f"{stamp}_{safe_name}"
    original_path.write_bytes(data)

    canonical_path = concept_path / CANONICAL_FILENAME
    try:
        df.to_parquet(canonical_path, index=False)
    except Exception as exc:
        raise DatasetError(
            f"Could not convert the upload to Parquet: {exc}. "
            "Check for mixed-type columns."
        ) from exc

    record = datasets_repo.add_file(
        conn,
        concept_id=concept.id,
        original_filename=filename,
        original_path=layout.relativize(config, original_path),
        canonical_path=layout.relativize(config, canonical_path),
        sheet_name=sheet_name if extension in EXCEL_EXTENSIONS else None,
        schema=schema,
        size_bytes=len(data),
        uploaded_by=uploaded_by,
    )
    audit_repo.record(
        conn, actor=actor, action="dataset.upload",
        target=f"{app.slug}:{concept.name}",
        details={"filename": filename, "bytes": len(data),
                 "sheet": sheet_name if extension in EXCEL_EXTENSIONS else None},
    )
    conn.commit()
    return record
