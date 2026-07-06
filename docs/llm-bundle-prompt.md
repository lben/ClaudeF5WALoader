# LLM bundle prompt — superseded by the authoring kit

**The recommended flow now lives in [`authoring_kit/`](../authoring_kit/):**
a system prompt plus companion guides that make the coding LLM
WALoader-aware from the very first message — design language applied from
the first preview, Dataset Concepts planned up front, tests always included,
and the bundle format built in. The WALoader operator wires those files into
the corporate LLM once (see `authoring_kit/README.md`); end users just chat.

## Fallback: priming an LLM mid-conversation

Use this only when someone already built an app with an *un-primed* LLM and
just needs a valid bundle out of it. Paste everything between the lines as
the final request:

---

Produce the complete project as ONE markdown file ("WALoader bundle"):

1. Start with a fenced code block whose info string is `toml waloader-bundle`
   declaring `bundle_format = 1` and `entrypoint = "app.py"`.
2. Then one section per file: a heading `## file: relative/path` followed by
   ONE fenced code block with that file's full content. If a file's content
   contains triple backticks, use a four-backtick fence for that file.
3. Rules: relative forward-slash paths only; no `..`; no hidden files except
   `.streamlit/` and `.gitignore`; no binaries or embedded datasets; include
   a `pyproject.toml` with `[project.dependencies]` (plain PyPI names only);
   include `tests/` with fast pytest tests and an empty root `conftest.py`.
4. Load platform data via:

   ```python
   try:
       from waloader_sdk.datasets import load_dataset, no_data_placeholder
   except ImportError:
       load_dataset = None  # local run: fall back to sample data
   ```

   and show *No data uploaded yet* when a dataset returns None. Apps using
   datasets must declare `pandas` and `pyarrow`.

---

Everything else (previews, design language, hand-holding, capability
questions) is what the authoring kit exists for — prefer it.
