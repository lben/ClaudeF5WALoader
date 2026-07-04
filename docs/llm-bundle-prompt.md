# Prompt template: make your coding LLM emit a WALoader bundle

Work with your coding LLM until the Streamlit app does what you want. Then
paste the block below (everything between the lines) as your final request.
Attach or paste it together with your app requirements — the LLM will return
one markdown file you can upload straight into WALoader's **Create new app**.

---

Produce the complete project as ONE markdown file ("WALoader bundle") with
exactly this structure:

1. Start with a fenced code block whose info string is `toml waloader-bundle`:

   ```toml waloader-bundle
   bundle_format = 1
   entrypoint = "app.py"
   ```

2. Then one section per file:

   ## file: relative/path.py

   followed by ONE fenced code block containing that file's full content.

Hard rules:
- Paths: relative, forward slashes, no `..`, no hidden files (only
  `.streamlit/` and `.gitignore` are allowed), nothing under `private/`.
- If a file's content itself contains triple backticks, use FOUR backticks
  (````) as that file's fence so it nests correctly.
- No binary files. Data files are uploaded separately through WALoader
  "Dataset Concepts" — do not embed data in the bundle.
- Include a `pyproject.toml` with `[project]` name, version and
  `dependencies = [...]` listing every import that is not in the standard
  library (streamlit, pandas, plotly, ...). Plain PyPI names only — no git
  URLs, no direct URLs, no local paths.
- Include a `tests/` folder with at least one fast pytest that imports the
  app's logic. These tests run before every deployment.

Platform APIs available to the app (WALoader injects them at runtime):

- Datasets. For each dataset concept I mention in [brackets], load it with:

  ```python
  from waloader_sdk.datasets import load_dataset, no_data_placeholder

  clients = load_dataset("clients")   # pandas DataFrame, or None if not uploaded
  if clients is None:
      no_data_placeholder(stop=True)  # shows italic "No data uploaded yet"
  ```

  Apps that use datasets must list `pandas` and `pyarrow` in dependencies.
  Wrap the import so the app also runs standalone during development:

  ```python
  try:
      from waloader_sdk.datasets import load_dataset, no_data_placeholder
  except ImportError:                      # running outside WALoader
      load_dataset = None
  ```

  and fall back to a small hard-coded sample DataFrame in that case.

- Login (only if I ask for "user management" / login support):

  ```python
  from waloader_sdk.auth import require_login, logout_button

  user = require_login()   # no-op if login is disabled for this app
  ```

- Do NOT hardcode ports, addresses, or baseUrlPath; do not call
  `st.set_page_config` more than once; do not read or write files outside the
  app's own folder.

My dataset concepts for this app: [clients], [transactions]   <-- edit this
The app entrypoint must be `app.py`.

---

## Tips

- If deployment fails, WALoader shows a copyable error block — paste it back
  to your LLM verbatim and ask for a corrected full bundle, then use the
  retry-upload button.
- Concept names are lowercase with underscores (`reference_data`), and must
  match `load_dataset("...")` calls exactly.
- Excel uploads ask for a sheet name (default `Sheet1`) — tell your data
  owners which sheet the app expects.
