# Client Positions — WALoader sample bundle

This file is a complete, working example of the WALoader bundle contract
(docs/markdown-bundle-contract.md). Upload it via "Create new app".

```toml waloader-bundle
bundle_format = 1
entrypoint = "app.py"
app_name = "Client Positions"
description = "Sample finance dashboard demonstrating Dataset Concepts"
```

The entrypoint. It uses the `[clients]` Dataset Concept through the WALoader
SDK, with a standalone fallback so the app also runs during local development.

## file: app.py

```python
import pandas as pd
import streamlit as st

from helpers import APP_TITLE, HELP_TEXT

st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

try:
    from waloader_sdk.datasets import load_dataset, no_data_placeholder

    IN_WALOADER = True
except ImportError:  # local development outside WALoader
    IN_WALOADER = False

if IN_WALOADER:
    clients = load_dataset("clients")
else:
    clients = pd.DataFrame(
        {"client": ["Acme Corp", "Globex", "Initech"], "aum_musd": [125.0, 89.5, 42.0]}
    )

if clients is None:
    no_data_placeholder(stop=True)

left, right = st.columns([1, 3])
left.metric("Clients", len(clients))
right.dataframe(clients, use_container_width=True)

with st.expander("How this app gets data"):
    st.markdown(HELP_TEXT)
```

A helper module whose content contains triple backticks — so its section uses
a four-backtick fence (the nested-fence rule):

## file: helpers.py

````python
APP_TITLE = "Client Positions"

HELP_TEXT = """
Data is mapped in WALoader under **Datasets → clients**. The app code loads it
with:

```python
from waloader_sdk.datasets import load_dataset
clients = load_dataset("clients")
```

Upload or replace the file there — no redeployment needed.
"""
````

## file: pyproject.toml

```toml
[project]
name = "client-positions"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["streamlit", "pandas", "pyarrow"]
```

## file: conftest.py

```python
# makes the app importable from tests/ when pytest runs at deploy time
```

## file: tests/test_smoke.py

```python
from helpers import APP_TITLE


def test_title() -> None:
    assert APP_TITLE == "Client Positions"
```

## file: .streamlit/config.toml

```toml
[theme]
base = "light"
```

## file: .gitignore

```
__pycache__/
```
