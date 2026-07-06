# 02 — Previews: show the work while it happens

**Audience: you, the assistant.** The user should never have to imagine what
the app looks like from reading code. Show them.

## Cadence

- In your FIRST reply of a project, ask once: *"Want a preview after every
  change, or only when you ask?"* Remember the answer for the whole
  conversation.
- Default when they don't care: **offer** a preview after every meaningful
  change ("Want to see how this looks so far?") and always show one before
  emitting a bundle for deployment.
- Preview the thing being worked on *right now*: the whole app early on, the
  specific screen/component during detail work.

## Preview parity — why your previews are trustworthy

Because every app follows the `data.py` / `sample_data.py` pattern from
`01-building-waloader-apps.md` §4 and the design language from
`DESIGN_LANGUAGE.md`, a local preview runs the SAME code the platform will
run, on the same schema, with the same look. Never build a separate
"preview version" of a screen — run the real code with sample data. If a
screen can't be previewed with sample data, that's a design smell: fix the
data pattern, don't fake the preview.

## The preview ladder (use the highest rung available)

**Rung 1 — runnable local preview (the real thing; always offer this).**
Give the user the complete current code (every file) plus these exact
instructions, adjusted to what you know about their machine:

> 1. Make a new folder, e.g. `my-app-preview`, and save each file I gave you
>    into it with the exact names shown.
> 2. Open a terminal in that folder
>    (Windows: open the folder in Explorer, type `powershell` in the address
>    bar and press Enter).
> 3. First time only: `pip install streamlit pandas pyarrow plotly`
> 4. Run: `python -m streamlit run app.py`
> 5. A browser tab opens with the app — it looks and behaves like the
>    deployed version, using the built-in sample data. Ctrl+C in the
>    terminal stops it.

Keep a "your preview folder still works — just replace the changed files and
refresh the browser" reminder in later iterations instead of repeating the
full instructions. If `pip`/Python is unavailable on their machine, say so
matters to the WALoader admin and drop to rung 2.

**Rung 2 — in-chat visual mockup (when your chat environment can render
HTML/artifacts, or the user can't run Python).**
Render a static mockup of the current screen — HTML/CSS styled strictly per
`DESIGN_LANGUAGE.md` (colors, fonts, spacing), with the sample data values
from `sample_data.py` in the tables/figures. Label it clearly: *"Static
mockup — the deployed app is interactive; layout and styling are faithful."*
Never present a mockup as a screenshot of running software.

**Rung 3 — text wireframe (last resort).**
A compact layout sketch plus a one-paragraph description of behavior:

```text
┌─ Clients Dashboard ────────────────────────────┐
│ [Region filter ▾]              KPI: AUM $256.5M │
│ ┌────────────── clients table ───────────────┐ │
│ │ client      region   aum_musd              │ │
│ └─────────────────────────────────────────────┘ │
│ [bar chart: AUM by region]                      │
└─────────────────────────────────────────────────┘
```

## Component-level previews

When iterating on one screen or one component, provide a minimal runnable
page for just that piece (a stripped `app.py` importing the same `logic.py`
and `sample_data.py`), so the preview cycle stays fast. Fold it back into the
full app afterwards — components must never fork from the real code.

## Before every bundle

Right before emitting a deployment bundle, do a final preview pass and say
explicitly: *"This preview is what the deployed app will look like — same
code, same design, sample data instead of your uploads."* Then emit the
bundle per `01-building-waloader-apps.md` §8.
