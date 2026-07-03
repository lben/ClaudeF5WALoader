from __future__ import annotations

import json
import sqlite3

from waloader.config import WALoaderConfig
from waloader.models import App
from waloader.services import bundles, layout, versioning

BUNDLE_TEXT = (
    "```toml waloader-bundle\n"
    "bundle_format = 1\n"
    'entrypoint = "app.py"\n'
    "```\n"
    "## file: app.py\n"
    "```python\n"
    "import streamlit as st\n"
    "st.write('v1')\n"
    "```\n"
    "## file: pages/two.py\n"
    "```python\n"
    "x = 2\n"
    "```\n"
)


def _create(conn: sqlite3.Connection, config: WALoaderConfig, app: App):
    parsed = bundles.parse_bundle(BUNDLE_TEXT)
    return versioning.create_version(
        conn, config, app, parsed, BUNDLE_TEXT.encode("utf-8"), created_by=app.owner_id
    )


class TestCreateVersion:
    def test_writes_source_tree_exactly(
        self, conn: sqlite3.Connection, config: WALoaderConfig, app: App
    ) -> None:
        version = _create(conn, config, app)
        assert version.version_number == 1

        source = layout.source_dir(config, app.slug, 1)
        assert (source / "app.py").read_text(encoding="utf-8") == (
            "import streamlit as st\nst.write('v1')\n"
        )
        assert (source / "pages" / "two.py").read_text(encoding="utf-8") == "x = 2\n"

    def test_preserves_uploaded_bundle_byte_exact(
        self, conn: sqlite3.Connection, config: WALoaderConfig, app: App
    ) -> None:
        _create(conn, config, app)
        preserved = layout.bundle_path(config, app.slug, 1)
        assert preserved.read_bytes() == BUNDLE_TEXT.encode("utf-8")

    def test_manifest(self, conn: sqlite3.Connection, config: WALoaderConfig, app: App) -> None:
        version = _create(conn, config, app)
        manifest = json.loads(
            layout.manifest_path(config, app.slug, 1).read_text(encoding="utf-8")
        )
        assert manifest["entrypoint"] == "app.py"
        assert manifest["version"] == 1
        assert {f["path"] for f in manifest["files"]} == {"app.py", "pages/two.py"}
        assert all(len(f["sha256"]) == 64 and f["size"] > 0 for f in manifest["files"])
        assert version.manifest["entrypoint"] == "app.py"  # same manifest in the DB row

    def test_version_numbers_increment_and_folders_zero_pad(
        self, conn: sqlite3.Connection, config: WALoaderConfig, app: App
    ) -> None:
        v1 = _create(conn, config, app)
        v2 = _create(conn, config, app)
        assert (v1.version_number, v2.version_number) == (1, 2)
        assert layout.version_dir(config, app.slug, 2).name == "000002"
        assert layout.version_dir(config, app.slug, 2).is_dir()

    def test_db_paths_relative_and_resolvable(
        self, conn: sqlite3.Connection, config: WALoaderConfig, app: App
    ) -> None:
        version = _create(conn, config, app)
        assert not version.source_path.startswith("/")
        assert "\\" not in version.source_path
        assert layout.resolve(config, version.source_path).is_dir()
        assert layout.resolve(config, version.bundle_path).is_file()
