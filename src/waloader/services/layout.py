"""App filesystem layout — the single source of truth for on-disk paths.

    data/apps/<slug>/
      versions/000001/{source/, manifest.json, uploaded_bundle.md}
      runtime/venvs/000001/
      datasets/<concept>/
      user_files/<app_user_id>/

DB rows store paths RELATIVE to data_dir (POSIX form) so a data directory can
be relocated with config only; ``resolve``/``relativize`` convert at the edges.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from waloader.config import WALoaderConfig


def version_dirname(number: int) -> str:
    return f"{number:06d}"


def app_dir(config: WALoaderConfig, slug: str) -> Path:
    return config.apps_dir / slug


def versions_dir(config: WALoaderConfig, slug: str) -> Path:
    return app_dir(config, slug) / "versions"


def version_dir(config: WALoaderConfig, slug: str, number: int) -> Path:
    return versions_dir(config, slug) / version_dirname(number)


def source_dir(config: WALoaderConfig, slug: str, number: int) -> Path:
    return version_dir(config, slug, number) / "source"


def manifest_path(config: WALoaderConfig, slug: str, number: int) -> Path:
    return version_dir(config, slug, number) / "manifest.json"


def bundle_path(config: WALoaderConfig, slug: str, number: int) -> Path:
    return version_dir(config, slug, number) / "uploaded_bundle.md"


def runtime_dir(config: WALoaderConfig, slug: str) -> Path:
    return app_dir(config, slug) / "runtime"


def venvs_root(config: WALoaderConfig, slug: str) -> Path:
    return runtime_dir(config, slug) / "venvs"


def venv_dir(config: WALoaderConfig, slug: str, version_number: int) -> Path:
    """Per-version venv: a new version installs beside the running old one, so
    updates never mutate the environment a live process is using (G01 §3.7)."""
    return venvs_root(config, slug) / version_dirname(version_number)


def datasets_dir(config: WALoaderConfig, slug: str) -> Path:
    return app_dir(config, slug) / "datasets"


def concept_dir(config: WALoaderConfig, slug: str, concept: str) -> Path:
    return datasets_dir(config, slug) / concept


def user_files_dir(config: WALoaderConfig, slug: str, app_user_id: int) -> Path:
    return app_dir(config, slug) / "user_files" / str(app_user_id)


def relativize(config: WALoaderConfig, path: Path) -> str:
    """Path under data_dir -> POSIX string stored in the DB."""
    return path.resolve().relative_to(config.data_dir).as_posix()


def resolve(config: WALoaderConfig, stored: str) -> Path:
    """DB-stored relative POSIX string -> absolute path under data_dir."""
    return config.data_dir.joinpath(*PurePosixPath(stored).parts)
