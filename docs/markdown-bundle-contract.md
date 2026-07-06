# Markdown project bundle contract

A bundle is **one UTF-8 markdown file** containing an entire Streamlit project.
WALoader validates it, reconstructs the files into a new app version, installs
dependencies, runs the app's tests, and deploys it. Every upload creates a new
version; the original bundle is preserved byte-exact alongside it.

See `docs/llm-bundle-prompt.md` for a copy-paste prompt that makes your coding
LLM emit a valid bundle, and `examples/sample-bundle.md` for a working example.

## 1. Metadata block (required, first)

The **first fenced code block** in the document must have the info string
`toml waloader-bundle` and a TOML body:

~~~markdown
```toml waloader-bundle
bundle_format = 1                  # required — only 1 is accepted
entrypoint = "app.py"              # required — must match one of the file sections
app_name = "My App"                # optional, informational (the UI field wins)
description = "..."                # optional
dataset_concepts = ["clients"]     # optional — see below
```
~~~

Prose before the metadata block is ignored. Unknown keys produce a warning,
not an error.

**`dataset_concepts`** (optional list of concept names): WALoader creates any
missing concepts automatically at deployment, so a freshly deployed app shows
its *No data uploaded yet* state instead of a missing-concept condition and
the owner only has to upload the data files. Invalid names are reported as
warnings in the deployment log and never fail the deployment.

## Tolerated upload artifacts

Real-world LLM exports add cruft; WALoader strips these at the upload
boundary (don't rely on them — emit clean bundles):

- Trailing `<workspaces-note>…</workspaces-note>` lines appended by corporate
  LLM workspaces are removed before parsing (the preserved original bundle
  keeps them; the reconstructed app never sees them).
- A single accidental outer ` ```markdown ` fence wrapped around the whole
  document (the classic copy-from-chat mistake) is unwrapped, provided the
  first fence carries no real info string and a matching closing fence ends
  the document.

## 2. File sections

Each file is declared with a level-2 heading followed by one fenced code block:

~~~markdown
## file: app.py

```python
import streamlit as st
st.title("Hello")
```

## file: pages/detail.py

```python
...
```
~~~

Rules:

- `## file: <relative/posix/path>` — `file:` is case-insensitive; flexible spacing.
- The **first** fenced block after the heading is the file's content, verbatim.
  Prose between sections is ignored (your LLM may annotate freely).
- Fences follow CommonMark: an opening fence of N ≥ 3 backticks closes at the
  first line of ≥ N backticks and nothing else.
- **Nested fences:** if a file's content contains ``` (very common — Streamlit
  apps embed markdown), wrap that file in a **longer** fence (4+ backticks):

  `````markdown
  ## file: notes.py

  ````python
  SNIPPET = """
  ```python
  example shown to end users
  ```
  """
  ````
  `````
- Empty files are allowed. Files are written UTF-8 with a trailing newline.

## 3. Path rules (violations reject the whole bundle, with a copyable error)

- Relative POSIX paths only: no absolute paths, no drive letters, no `\`.
- No `..`, no `.` segments, no empty segments (`//`).
- Nothing at or under `private/`.
- No hidden files/directories, **except** `.streamlit/` (Streamlit config) and
  a top-level `.gitignore`.
- No duplicate paths. The declared entrypoint must be among the files.
- Limits (configurable): bundle ≤ `uploads.max_markdown_bundle_mb` (default
  10 MB), ≤ `uploads.max_bundle_files` files (default 200).

## 4. What you may include

- `pyproject.toml` (optional): `[project.dependencies]` is read and installed
  into the app's own virtualenv, subject to the dependency policy
  (`docs/dependency-policy.md`). Without it, the approved base set is installed
  (streamlit, pandas, plotly, duckdb, pyarrow).
- `tests/` or `test_*.py` files (optional): they run with pytest **before**
  deployment; failures abort the deployment with the output shown to you.
- `.streamlit/config.toml` (optional): theme etc. Do not set `server.*` values —
  WALoader controls port/address/baseUrlPath at launch.

## 5. What you cannot include

- Binary files. Bundles are text-only; tabular data reaches your app through
  Dataset Concepts (`docs/dataset-concepts-contract.md`).
- Anything that must survive redeployment *outside* the source tree — each
  upload is a fresh version directory.

## 6. Reconstruction guarantees

- Files are written only inside the new version's `source/` directory —
  reconstruction can never touch WALoader's own files.
- Storage layout: `data/apps/<slug>/versions/000001/{source/, manifest.json,
  uploaded_bundle.md}` with a manifest of paths, sizes, and sha256 hashes.
