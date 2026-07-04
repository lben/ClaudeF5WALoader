# Dataset Concepts

Dataset Concepts decouple *data* from *code*: the app code refers to stable
concept names (`clients`, `transactions`, `positions`, …) and finance users
upload/replace the actual files in WALoader without redeploying the app.

## For app authors (and their LLMs)

```python
from waloader_sdk.datasets import load_dataset, no_data_placeholder

clients = load_dataset("clients")        # DataFrame, or None when nothing uploaded
if clients is None:
    no_data_placeholder(stop=True)       # italic "No data uploaded yet", stops run
```

- `load_dataset(name, required=True)` raises `DatasetNotUploadedError` instead
  of returning None.
- Unknown concept name → `UnknownConceptError` (define it in the mapping screen).
- Outside WALoader (local development) the import or call raises
  `WALoaderEnvError` — see `docs/llm-bundle-prompt.md` for the recommended
  standalone fallback pattern.
- Apps using datasets must declare `pandas` and `pyarrow` in their
  dependencies. The SDK reads a canonical **Parquet** copy, so apps never need
  Excel readers regardless of what file the user uploaded.

## For app owners (the mapping screen)

Dashboard → **Datasets** page → select the app:

1. Type a concept name and **Save** (lowercase letters/digits/underscores,
   starting with a letter).
2. Each saved concept row shows: name, current file (or *No data uploaded
   yet*), an upload control, and **Delete** (with confirmation).
3. Upload accepts `.csv` (the default expected format), `.xlsx`, `.xls`,
   `.parquet` — limit `uploads.max_dataset_file_mb` (default 250 MB).
4. **Excel files require a sheet name.** The input is pre-filled with
   `Sheet1`; the chosen sheet is stored with the file's metadata and used for
   reading, schema inference, and future comparisons. CSV/parquet never ask
   for a sheet.
5. **Replacements are schema-checked.** WALoader compares column names and
   inferred types of the new file against the current one and shows added /
   removed / type-changed columns in a copyable block. Confirm to overwrite —
   a mismatch may be perfectly fine if the app code was updated for the new
   schema.

## Storage details

- Files live under `data/apps/<slug>/datasets/<concept>/`: every original is
  kept (timestamped) plus one canonical `current.parquet` that apps read.
- Schema (column → dtype) is inferred at upload time and stored in the DB.
- Deleting a concept removes its directory after confirmation.
