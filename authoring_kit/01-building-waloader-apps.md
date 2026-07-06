# 01 — Building WALoader apps (the engineering contract)

**Audience: you, the assistant.** These rules are binding for every app you
produce. The user never needs to read this file.

## 1. What WALoader does with your output

The user uploads ONE markdown file (the *bundle*, format in §8) into WALoader.
WALoader then: validates it → recreates the project files → installs the
dependencies you declared into an isolated virtualenv → **runs your tests
(failures block the deployment)** → starts the app → gives the user a URL.

- **Updates:** the user uploads a new complete bundle for the same app. Same
  URL, new version. If your new version fails to build or test, the old
  version keeps running — safe to iterate.
- **Failures:** WALoader shows the user a copyable error block (dependency,
  test, and runtime output concatenated). Expect them to paste it back to
  you; fix and re-emit a complete bundle.
- **Data:** uploaded separately as Dataset Concepts (§4) and replaceable at
  any time *without* redeploying the app.

## 2. Project shape

Every app follows this layout (small apps may omit `pages/`):

```text
app.py                    # entrypoint — always exactly this name
pages/…                   # optional extra Streamlit pages
data.py                   # THE ONLY module that loads data (see §4)
sample_data.py            # realistic fake data for previews + tests (see §4)
logic.py                  # calculations/transforms, importable + testable
conftest.py               # empty file at root (makes tests able to import the app)
tests/test_*.py           # pytest tests (see §6)
pyproject.toml            # name/version + [project.dependencies] (see §7)
.streamlit/config.toml    # optional theme (mirror DESIGN_LANGUAGE.md tokens)
```

Rules: keep UI (`app.py`, `pages/`) thin — rendering only. Put every
calculation, filter, aggregation, or transformation in `logic.py` (or similar
modules) as plain functions of DataFrames in / DataFrames out. That is what
makes the app testable and what keeps previews honest. Call
`st.set_page_config(...)` exactly once, first Streamlit call in `app.py`.

## 3. Design language

`DESIGN_LANGUAGE.md` (attached alongside this file) defines the visual
standard: colors, typography, spacing, component conventions, tone. Apply it
to **every screen from the first draft** — layout, chart colors, empty
states, labels. If it defines color tokens, also derive
`.streamlit/config.toml`'s `[theme]` from them so the app chrome matches.
Previews must follow it too (see `02-previews.md`) — the first preview the
user sees should look like the deployed app, not like a placeholder.

## 4. Data: Dataset Concepts (and the preview-parity pattern)

Apps never embed real data and never read arbitrary files. Data arrives
through **Dataset Concepts**: named datasets the user uploads in WALoader
(CSV/Excel/Parquet) and the app loads by name. Concept names are lowercase
letters, digits and underscores, starting with a letter — e.g. `clients`,
`transactions`, `reference_data`. Users often write them in [brackets] when
describing the app; treat `[clients]` as "there will be a concept named
clients".

**All data loading goes through this exact pattern** — one `data.py` module,
with a sample-data fallback so the app runs identically as a local preview
and as a deployed app:

```python
# data.py — the only place the app loads data
import pandas as pd


def load(concept: str) -> pd.DataFrame | None:
    """Deployed: reads the WALoader dataset. Preview: reads sample_data."""
    try:
        from waloader_sdk.datasets import load_dataset
    except ImportError:          # local preview — WALoader not present
        import sample_data
        return sample_data.get(concept)
    return load_dataset(concept)  # DataFrame, or None if nothing uploaded yet


def empty_state(concept: str) -> None:
    try:
        from waloader_sdk.datasets import no_data_placeholder
        no_data_placeholder(stop=True)
    except ImportError:
        import streamlit as st
        st.markdown("*No data uploaded yet*")
        st.stop()
```

```python
# sample_data.py — realistic fake data, SAME columns and dtypes the app expects
import pandas as pd

_FRAMES = {
    "clients": pd.DataFrame({
        "client": ["Acme Corp", "Globex", "Initech"],
        "region": ["EMEA", "AMER", "APAC"],
        "aum_musd": [125.0, 89.5, 42.0],
    }),
}


def get(concept: str) -> pd.DataFrame:
    return _FRAMES[concept].copy()
```

Non-negotiables:

- **Every concept the app uses has a sample frame with exactly the columns
  and types the app expects.** This is the parity guarantee: the preview
  behaves like production because it exercises the same code paths on the
  same schema.
- Always handle `load(...) is None` (concept defined, nothing uploaded yet)
  by showing the standard italic *No data uploaded yet* via `empty_state`.
- Tell the user, in plain language, the expected columns for each concept
  ("your clients file needs columns: client, region, aum_musd") — they
  upload real files themselves, and WALoader schema-checks replacements.
- Apps that use datasets must declare `pandas` and `pyarrow` in dependencies.

## 5. Login (only when the user asks for it)

If the user wants their app protected by a login, use the WALoader user
management SDK — never roll your own:

```python
from waloader_sdk.auth import require_login, logout_button

user = require_login()   # no-op (None) when login is disabled for this app
```

Guard the import with try/except ImportError like `data.py` does (local
previews skip login). Tell the user to switch on *Users Management Support*
for the app in WALoader and create their app users there. Do NOT add
`argon2-cffi` or any auth library to dependencies — WALoader provides it.

## 6. Tests (mandatory)

Every bundle ships pytest tests; WALoader runs them before every deployment
and a red test stops the release — that's a feature, use it.

- Test the `logic.py` functions against `sample_data` frames: filters,
  aggregations, edge cases (empty frame, missing values, one row).
- Do NOT test the Streamlit UI or open browsers; do not hit the network.
- Keep the suite fast (seconds — the platform enforces a timeout, default
  5 minutes).
- `conftest.py` (empty) at the project root makes `logic`/`sample_data`
  importable from `tests/`.

```python
# tests/test_logic.py
import sample_data
from logic import total_aum


def test_total_aum() -> None:
    assert total_aum(sample_data.get("clients")) == 256.5
```

## 7. Dependencies

Declare them in `pyproject.toml`:

```toml
[project]
name = "clients-dashboard"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["streamlit", "pandas", "pyarrow", "plotly"]
```

- **Plain PyPI names only.** No git URLs, no direct file URLs, no local
  paths — the platform rejects those.
- Keep the list minimal. Safe default palette: `streamlit`, `pandas`,
  `pyarrow`, `plotly`, `duckdb`, `openpyxl`, `numpy`. Anything exotic may be
  blocked by policy — prefer the palette.
- Never declare `argon2-cffi` (platform-provided) or pin exact versions
  unless something breaks without it.

## 8. The bundle — your final deliverable, exactly this format

One UTF-8 markdown file containing the whole project. Emit it only when the
user wants to deploy (or asks for the file), and ALWAYS complete — every
file, full contents, never "unchanged from before".

**Structure:** a metadata block first, then one section per file.

`````markdown
# Clients Dashboard — WALoader bundle

```toml waloader-bundle
bundle_format = 1
entrypoint = "app.py"
app_name = "Clients Dashboard"
description = "AUM by client and region"
```

## file: app.py

```python
import streamlit as st
# ... full file contents ...
```

## file: logic.py

```python
# ... full file contents ...
```
`````

Rules the platform enforces (violations reject the whole upload):

- The **first fenced code block** must have info string `toml waloader-bundle`
  and declare `bundle_format = 1` and `entrypoint = "app.py"`.
- Each file starts with a level-2 heading `## file: relative/path` followed by
  ONE fenced code block containing the file's full content, verbatim. Prose
  between sections is fine (annotate freely).
- **Nested fences:** if a file's content itself contains triple backticks
  (docstrings with markdown, `st.markdown` code samples…), open that file's
  fence with FOUR backticks (````) so it nests correctly. When unsure, four
  backticks are always safe.
- Paths: relative, forward slashes only; no `..`, no absolute paths, no
  hidden files except `.streamlit/…` and a top-level `.gitignore`; nothing
  under `private/`.
- Text files only — no binaries, no embedded datasets (data goes through
  Dataset Concepts). Limits (defaults): 10 MB per bundle, 200 files.
- The entrypoint must be among the file sections.

After emitting a bundle, walk the user through deployment: WALoader →
*Create new app* (or the app's gear → *Update code*) → pick the name →
upload the file → then *Datasets* page → create each concept → upload their
files.

## 9. Hard don'ts (any of these breaks the deployed app)

- Don't set `server.port`, `server.address`, `server.baseUrlPath`, or any
  `[server]` value in `.streamlit/config.toml` — WALoader controls those.
- Don't call `st.set_page_config` more than once.
- Don't read or write files outside the app's own folder; don't treat local
  file writes as durable (they vanish on the next update — durable data goes
  through Dataset Concepts).
- Don't put secrets, tokens, passwords, or real client data in code or
  sample data.
- Don't start threads, schedulers, or background jobs; don't send emails;
  don't call external APIs unless the user confirms the corporate network
  allows it.
