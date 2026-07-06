from __future__ import annotations

import io
import sqlite3

import pandas as pd
import pytest

from waloader.config import WALoaderConfig
from waloader.models import App
from waloader.repositories import datasets as datasets_repo
from waloader.services import datasets_service as ds
from waloader.services import layout

DF_V1 = pd.DataFrame({"id": [1, 2], "name": ["a", "b"]})
DF_V2 = pd.DataFrame({"id": [1.5, 2.5], "region": ["x", "y"]})  # id type change, name->region

# dtype *names* differ across pandas versions ("object" vs "str") — derive, don't hardcode
V1_SCHEMA = {col: str(dtype) for col, dtype in DF_V1.dtypes.items()}


def csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def parquet_bytes(df: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    df.to_parquet(buffer, index=False)
    return buffer.getvalue()


def xlsx_bytes(sheets: dict[str, pd.DataFrame]) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)
    return buffer.getvalue()


def xls_bytes(df: pd.DataFrame, sheet: str) -> bytes:
    xlwt = pytest.importorskip("xlwt", reason="xlwt needed to write .xls fixtures")
    book = xlwt.Workbook()
    ws = book.add_sheet(sheet)
    for col_index, col in enumerate(df.columns):
        ws.write(0, col_index, str(col))
        for row_index, value in enumerate(df[col], start=1):
            ws.write(row_index, col_index, value)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


class TestConcepts:
    def test_create_valid(self, conn: sqlite3.Connection, app: App) -> None:
        concept = ds.create_concept(conn, app, "clients", actor="alice")
        assert concept.name == "clients"

    @pytest.mark.parametrize("bad", ["Clients", "1clients", "cli ents", "cli-ents", ""])
    def test_invalid_names(self, conn: sqlite3.Connection, app: App, bad: str) -> None:
        with pytest.raises(ds.DatasetError, match="Concept names"):
            ds.create_concept(conn, app, bad)

    def test_duplicate(self, conn: sqlite3.Connection, app: App) -> None:
        ds.create_concept(conn, app, "clients")
        with pytest.raises(ds.DatasetError, match="already exists"):
            ds.create_concept(conn, app, "clients")

    def test_delete_removes_disk_files(self, conn: sqlite3.Connection,
                                       config: WALoaderConfig, app: App) -> None:
        concept = ds.create_concept(conn, app, "clients")
        ds.store_upload(conn, config, app, concept,
                        filename="c.csv", data=csv_bytes(DF_V1))
        concept_dir = layout.concept_dir(config, app.slug, "clients")
        assert concept_dir.exists()
        ds.delete_concept(conn, config, app, concept.id)
        assert not concept_dir.exists()
        assert datasets_repo.list_concepts(conn, app.id) == []


class TestUploads:
    def test_csv_upload_canonical_parquet(self, conn, config, app: App) -> None:
        concept = ds.create_concept(conn, app, "clients")
        record = ds.store_upload(conn, config, app, concept,
                                 filename="clients v1.csv", data=csv_bytes(DF_V1))
        assert record.is_current == 1
        assert record.sheet_name is None
        assert record.schema == V1_SCHEMA
        canonical = layout.resolve(config, record.canonical_path)
        assert canonical.name == "current.parquet"
        pd.testing.assert_frame_equal(pd.read_parquet(canonical), DF_V1)
        original = layout.resolve(config, record.original_path)
        assert original.exists() and "clients_v1.csv" in original.name

    def test_parquet_upload(self, conn, config, app: App) -> None:
        concept = ds.create_concept(conn, app, "positions")
        record = ds.store_upload(conn, config, app, concept,
                                 filename="p.parquet", data=parquet_bytes(DF_V1))
        assert record.schema == V1_SCHEMA

    def test_csv_ignores_sheet_name(self, conn, config, app: App) -> None:
        concept = ds.create_concept(conn, app, "clients")
        record = ds.store_upload(conn, config, app, concept, filename="c.csv",
                                 data=csv_bytes(DF_V1), sheet_name="Sheet1")
        assert record.sheet_name is None  # only Excel stores a sheet

    def test_extension_rejected(self, conn, config, app: App) -> None:
        concept = ds.create_concept(conn, app, "clients")
        with pytest.raises(ds.DatasetError, match="not allowed"):
            ds.store_upload(conn, config, app, concept,
                            filename="c.txt", data=b"id\n1\n")

    def test_size_limit(self, conn, tmp_path, app: App) -> None:
        config = WALoaderConfig.model_validate(
            {"paths": {"data_dir": str(tmp_path / "d")},
             "uploads": {"max_dataset_file_mb": 1}}
        )
        concept = ds.create_concept(conn, app, "clients")
        big = b"x" * (2 * 1024 * 1024)
        with pytest.raises(ds.DatasetError, match="limit is 1 MB"):
            ds.store_upload(conn, config, app, concept, filename="c.csv", data=big)


class TestExcel:
    def test_sheet_required_for_xlsx(self, conn, config, app: App) -> None:
        concept = ds.create_concept(conn, app, "clients")
        data = xlsx_bytes({"Sheet1": DF_V1})
        with pytest.raises(ds.DatasetError, match="sheet name is required"):
            ds.store_upload(conn, config, app, concept, filename="c.xlsx", data=data)
        with pytest.raises(ds.DatasetError, match="sheet name is required"):
            ds.store_upload(conn, config, app, concept, filename="c.xlsx",
                            data=data, sheet_name="   ")

    def test_missing_sheet_lists_available(self, conn, config, app: App) -> None:
        concept = ds.create_concept(conn, app, "clients")
        data = xlsx_bytes({"Sheet1": DF_V1, "Positions": DF_V2})
        with pytest.raises(ds.DatasetError, match="Available sheets.*Positions"):
            ds.store_upload(conn, config, app, concept, filename="c.xlsx",
                            data=data, sheet_name="Nope")

    def test_sheet_stored_and_used_for_schema(self, conn, config, app: App) -> None:
        concept = ds.create_concept(conn, app, "clients")
        data = xlsx_bytes({"Sheet1": DF_V1, "Positions": DF_V2})
        record = ds.store_upload(conn, config, app, concept, filename="c.xlsx",
                                 data=data, sheet_name="Positions")
        assert record.sheet_name == "Positions"
        assert set(record.schema) == {"id", "region"}  # the chosen sheet's schema

    def test_default_sheet1_from_config_matches_excel_default(
        self, conn, config, app: App
    ) -> None:
        assert config.uploads.default_excel_sheet_name == "Sheet1"
        concept = ds.create_concept(conn, app, "clients")
        record = ds.store_upload(
            conn, config, app, concept, filename="c.xlsx",
            data=xlsx_bytes({"Sheet1": DF_V1}),
            sheet_name=config.uploads.default_excel_sheet_name,
        )
        assert record.sheet_name == "Sheet1"

    def test_legacy_xls(self, conn, config, app: App) -> None:
        concept = ds.create_concept(conn, app, "legacy")
        data = xls_bytes(DF_V1, "Sheet1")
        with pytest.raises(ds.DatasetError, match="sheet name is required"):
            ds.store_upload(conn, config, app, concept, filename="c.xls", data=data)
        record = ds.store_upload(conn, config, app, concept, filename="c.xls",
                                 data=data, sheet_name="Sheet1")
        assert record.sheet_name == "Sheet1"
        assert set(record.schema) == {"id", "name"}


class TestSchemaDiff:
    def test_diff_categories(self) -> None:
        old = {"id": "int64", "name": "object", "amount": "float64"}
        new = {"id": "float64", "region": "object", "amount": "float64"}
        diff = ds.diff_schemas(old, new)
        assert diff.added == {"region": "object"}
        assert diff.removed == {"name": "object"}
        assert diff.changed == {"id": ("int64", "float64")}
        assert diff.has_changes
        block = diff.format()
        assert "+ added   region (object)" in block
        assert "- removed name (was object)" in block
        assert "~ changed id: int64 -> float64" in block

    def test_no_changes(self) -> None:
        schema = {"id": "int64"}
        diff = ds.diff_schemas(schema, dict(schema))
        assert not diff.has_changes
        assert diff.format() == "No schema changes."


class TestReplacementFlow:
    def test_first_upload_has_empty_diff(self, conn, config, app: App) -> None:
        concept = ds.create_concept(conn, app, "clients")
        diff = ds.replacement_diff(conn, config, concept, "c.csv",
                                   csv_bytes(DF_V1), None)
        assert not diff.has_changes

    def test_replacement_diff_and_store(self, conn, config, app: App) -> None:
        concept = ds.create_concept(conn, app, "clients")
        first = ds.store_upload(conn, config, app, concept,
                                filename="v1.csv", data=csv_bytes(DF_V1))
        diff = ds.replacement_diff(conn, config, concept, "v2.csv",
                                   csv_bytes(DF_V2), None)
        assert diff.has_changes
        assert "region" in diff.added and "name" in diff.removed
        assert diff.changed["id"] == ("int64", "float64")

        # ... UI shows the diff, user confirms, then:
        second = ds.store_upload(conn, config, app, concept,
                                 filename="v2.csv", data=csv_bytes(DF_V2))
        current = datasets_repo.current_file(conn, concept.id)
        assert current.id == second.id
        canonical = pd.read_parquet(layout.resolve(config, current.canonical_path))
        assert list(canonical.columns) == ["id", "region"]
        # both originals preserved
        assert layout.resolve(config, first.original_path).exists()
        assert layout.resolve(config, second.original_path).exists()

    def test_replacement_diff_uses_sheets_on_both_sides(
        self, conn, config, app: App
    ) -> None:
        concept = ds.create_concept(conn, app, "clients")
        ds.store_upload(conn, config, app, concept, filename="v1.xlsx",
                        data=xlsx_bytes({"Data": DF_V1}), sheet_name="Data")
        diff = ds.replacement_diff(
            conn, config, concept, "v2.xlsx",
            xlsx_bytes({"Other": DF_V1, "Data2": DF_V2}), "Data2",
        )
        assert "region" in diff.added  # compared old 'Data' schema vs new 'Data2'


class TestSdk:
    @pytest.fixture
    def sdk_env(self, conn, config, app: App, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("WALOADER_APP_SLUG", app.slug)
        monkeypatch.setenv("WALOADER_APP_NAME", app.name)
        monkeypatch.setenv("WALOADER_DB_PATH", str(config.database_path))
        monkeypatch.setenv("WALOADER_DATA_DIR", str(config.data_dir))
        monkeypatch.setenv(
            "WALOADER_DATASETS_DIR", str(layout.datasets_dir(config, app.slug))
        )
        # the conftest conn uses tmp_path/test.db; SDK opens its own connection,
        # so point a real DB file at the config location and mirror the schema.
        import waloader.db as wdb

        real = wdb.connect(config.database_path)
        wdb.migrate(real)
        yield real
        real.close()

    def _seed(self, real_conn, config, app: App, with_file: bool) -> None:
        from waloader.repositories import apps as apps_repo
        from waloader.repositories import users as users_repo

        user = users_repo.create(real_conn, "owner", "o@x.com", "h")
        stored_app = apps_repo.create(
            real_conn, owner_id=user.id, name=app.name, slug=app.slug
        )
        concept = datasets_repo.create_concept(real_conn, stored_app.id, "clients")
        if with_file:
            ds.store_upload(
                real_conn, config, stored_app, concept,
                filename="c.csv", data=csv_bytes(DF_V1),
            )
        real_conn.commit()

    def test_load_dataset_roundtrip(self, sdk_env, config, app: App) -> None:
        from waloader_sdk.datasets import load_dataset

        self._seed(sdk_env, config, app, with_file=True)
        df = load_dataset("clients")
        pd.testing.assert_frame_equal(df, DF_V1)

    def test_not_uploaded_returns_none_or_raises(self, sdk_env, config,
                                                 app: App) -> None:
        from waloader_sdk.datasets import DatasetNotUploadedError, load_dataset

        self._seed(sdk_env, config, app, with_file=False)
        assert load_dataset("clients") is None
        with pytest.raises(DatasetNotUploadedError, match="No data uploaded yet"):
            load_dataset("clients", required=True)

    def test_unknown_concept_is_soft_by_default(self, sdk_env, config,
                                                app: App) -> None:
        """Field lesson: an app whose concept isn't defined yet must show its
        friendly empty state, not crash — None unless required=True."""
        from waloader_sdk.datasets import UnknownConceptError, load_dataset

        self._seed(sdk_env, config, app, with_file=False)
        assert load_dataset("ghosts") is None
        with pytest.raises(UnknownConceptError, match="not defined"):
            load_dataset("ghosts", required=True)

    def test_missing_env_raises_helpfully(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from waloader_sdk.datasets import WALoaderEnvError, load_dataset

        for var in ("WALOADER_APP_SLUG", "WALOADER_DB_PATH", "WALOADER_DATA_DIR"):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(WALoaderEnvError, match="injects when it launches"):
            load_dataset("clients")

    def test_no_data_placeholder_runs_without_streamlit_app(self) -> None:
        from waloader_sdk.datasets import no_data_placeholder

        no_data_placeholder()  # bare-mode streamlit call must not raise
