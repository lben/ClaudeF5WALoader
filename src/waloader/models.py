"""Domain models — thin dataclass views over DB rows."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, fields
from typing import Any, Self


class _RowModel:
    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Self:
        names = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        data = {key: row[key] for key in row.keys() if key in names}
        return cls(**data)  # type: ignore[call-arg]


@dataclass
class User(_RowModel):
    id: int
    username: str
    email: str
    password_hash: str
    is_admin: int
    is_active: int
    created_at: str
    updated_at: str


@dataclass
class App(_RowModel):
    id: int
    owner_id: int
    name: str
    slug: str
    description: str
    state: str
    current_version: int | None
    port: int | None
    caddy_route: str | None
    user_mgmt_enabled: int
    last_deploy_error: str | None
    created_at: str
    updated_at: str
    deleted_at: str | None
    archive_path: str | None
    purge_after: str | None


@dataclass
class AppVersion(_RowModel):
    id: int
    app_id: int
    version_number: int
    manifest_json: str
    bundle_path: str
    source_path: str
    created_by: int | None
    created_at: str

    @property
    def manifest(self) -> dict[str, Any]:
        return json.loads(self.manifest_json)


@dataclass
class Deployment(_RowModel):
    id: int
    app_id: int
    version_id: int | None
    kind: str
    status: str
    error_summary: str | None
    log_path: str | None
    started_at: str
    finished_at: str | None


@dataclass
class AppRuntime(_RowModel):
    app_id: int
    pid: int | None
    pid_create_time: float | None
    started_at: str | None
    last_check_at: str | None
    last_healthy_at: str | None
    last_failed_at: str | None
    last_failure_reason: str | None
    consecutive_failures: int
    deployed_healthy: int


@dataclass
class DatasetConcept(_RowModel):
    id: int
    app_id: int
    name: str
    created_at: str


@dataclass
class DatasetFile(_RowModel):
    id: int
    concept_id: int
    original_filename: str
    original_path: str
    canonical_path: str
    sheet_name: str | None
    schema_json: str
    size_bytes: int
    is_current: int
    uploaded_by: int | None
    uploaded_at: str

    @property
    def schema(self) -> dict[str, str]:
        return json.loads(self.schema_json)


@dataclass
class AppUser(_RowModel):
    id: int
    app_id: int
    username: str
    email: str
    password_hash: str
    is_active: int
    observations: str
    created_at: str
    updated_at: str


@dataclass
class AppUserAttachment(_RowModel):
    id: int
    app_user_id: int
    filename: str
    stored_path: str
    note: str
    uploaded_at: str
