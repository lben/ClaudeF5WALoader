"""Dataset access for child apps.

    from waloader_sdk.datasets import load_dataset, no_data_placeholder

    clients = load_dataset("clients")     # DataFrame, or None if nothing uploaded
    if clients is None:
        no_data_placeholder(stop=True)    # italic "No data uploaded yet" + st.stop()

Reads only the canonical Parquet that WALoader wrote at upload time — apps
using datasets need pandas + pyarrow declared, nothing else.
"""

from __future__ import annotations

from waloader_sdk._context import WALoaderEnvError, connect, get_context

__all__ = [
    "DatasetNotUploadedError",
    "UnknownConceptError",
    "WALoaderEnvError",
    "load_dataset",
    "no_data_placeholder",
]


class UnknownConceptError(KeyError):
    pass


class DatasetNotUploadedError(RuntimeError):
    pass


def load_dataset(name: str, *, required: bool = False, app_slug: str | None = None):
    """Load the dataset mapped to a concept name as a pandas DataFrame.

    Returns None when there is no data to load yet — whether the concept has
    no uploaded file OR the concept has not been defined in WALoader at all.
    Non-technical owners fix both the same way (Datasets page), so the app
    should show its friendly "No data uploaded yet" state, never a traceback.

    ``required=True`` turns the soft cases into hard errors for apps that
    cannot render without data: UnknownConceptError (concept not defined) or
    DatasetNotUploadedError (defined, nothing uploaded).
    """
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - depends on child venv
        raise RuntimeError(
            "load_dataset needs pandas (and pyarrow). Add both to your app's "
            "pyproject.toml dependencies."
        ) from exc

    context = get_context(app_slug)
    conn = connect(context)
    try:
        row = conn.execute(
            """SELECT df.canonical_path
               FROM dataset_concepts dc
               JOIN apps a ON a.id = dc.app_id
               LEFT JOIN dataset_files df ON df.concept_id = dc.id AND df.is_current = 1
               WHERE a.slug = ? AND dc.name = ?""",
            (context.app_slug, name),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        if required:
            raise UnknownConceptError(
                f"Dataset concept {name!r} is not defined for app "
                f"{context.app_slug!r}. Define it in WALoader's Dataset "
                "Concepts screen."
            )
        return None
    if row["canonical_path"] is None:
        if required:
            raise DatasetNotUploadedError(
                f"No data uploaded yet for concept {name!r}."
            )
        return None
    return pd.read_parquet(context.resolve(row["canonical_path"]))


def no_data_placeholder(message: str = "No data uploaded yet", *, stop: bool = False):
    """Render the standard italic empty-state message (Streamlit if available)."""
    try:
        import streamlit as st
    except ImportError:  # pragma: no cover - non-streamlit contexts
        print(message)
        return
    st.markdown(f"*{message}*")
    if stop:
        st.stop()
