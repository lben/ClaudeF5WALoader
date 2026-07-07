# AGENTS.md — WALoader agent contract

This file contains the durable operating rules for any coding agent working in this
repository (Claude Code / Fable 5 at home, GPT 5.5 + /plan at work). It deliberately
contains **rules only**. The complete product specification and build plan live in the
active goal file under `goals/` (currently `goals/G02-backups-reset-migration.md`;
`goals/G01-waloader-complete.md` is complete — v0.1.0 — and remains the base spec of
record for everything G02 does not touch). `WALoaderInitialWriteup.txt` is the
historical first draft — do not implement from it; where it disagrees with the goal
files, the goal files win.

## What WALoader is

WALoader is a Streamlit-based internal platform for finance users who are not software
developers but can work with LLM coding agents. It ingests LLM-generated Streamlit
projects as structured markdown bundles, validates and reconstructs them, deploys them
as managed child apps behind an optional Caddy reverse proxy, and provides lifecycle
management, dataset mapping (Dataset Concepts), reusable user management, health
checks, crash notifications, backups, and operator tooling.

## Workflow: one goal, tracked progress

This project uses a single-goal workflow, not per-session milestones.

Before doing any work, read in this order:

1. `AGENTS.md` (this file)
2. The active goal file in `goals/`
3. `PROGRESS.md` (if it exists)
4. `DEVLOG.md` (if it exists — skim the latest entries)

Rules:

- Implement the goal's build phases **in order, bottom-up**: services before their
  clients, dependencies before dependents. Never build a client of a service that does
  not exist yet, except as a small interface stub that is documented in `PROGRESS.md`
  and replaced before the phase is considered complete.
- Do not build the UI first. Service layer first; the UI and CLIs are thin clients of
  the same services.
- Do not stop after partial scaffolding or analysis when implementation was requested.
  Continue until the goal's Definition of Done is met or a concrete blocker is hit.
- Sessions may be interrupted or their context compacted. Work so that a fresh session
  can resume from `PROGRESS.md` + `DEVLOG.md` alone.
- Do not implement broad future architecture that the current phase does not need.
- Keep the system as simple as possible while satisfying the goal file.
- Prefer automation over manual operator instructions; when both exist, implement
  automation first and document the manual fallback second.

### PROGRESS.md (living status — keep current)

Must always show: the active goal, current phase, per-phase checklist with done/pending
marks, active acceptance criteria, last validation commands and their results, and
known blockers. Update it at every phase boundary and whenever validation is run.

### DEVLOG.md (append-only log)

Append an entry at every phase boundary and at every session end: date, phase, summary,
files changed, tests added/changed, validation commands run, results, known issues,
next recommended action. Never rewrite past entries except for pure formatting. If
blocked, record the exact error text, what was attempted, and the next recommended
action before stopping.

## Non-negotiable rules

### Protected `private/` directory and secrets

- The protected location is the **repository-local `private/` directory** (i.e.
  `<repo-root>/private/`). It does not currently exist; the rules still apply if it
  ever appears. Do not read, list, search, summarize, modify, move, delete, back up,
  or infer anything under it; do not generate code that reads from or writes to it;
  never include it in logs, searches, bundle parsing, dataset handling, Caddy config,
  process management, backups, or cleanup.
- **macOS note:** the operating system's `/private` (which backs `/tmp`, `/var`,
  `/etc`) is an unrelated OS directory and is NOT the protected folder. Normal use of
  system temp directories is fine. The protection applies to the repo-relative
  `private/` path only — and, inside uploaded bundles, to any relative path beginning
  with `private/`.
- Never open, read, print, summarize, copy, inspect, or log the **contents** of any
  configured uv config file (e.g. a corporate `uv.toml`). Referencing its *path* in
  config and commands is expected and allowed.
- Never hardcode or commit secrets, tokens, passwords, package-index credentials, or
  private certificate material. Never log token-bearing URLs; redact credentials from
  any surfaced command output or error text.
- Never store plaintext passwords; use `argon2-cffi` hashing.

### Quality

- Do not weaken, skip, or delete tests merely to make them pass. Change existing tests
  only when intended behavior changes, and record the behavior change in `DEVLOG.md`.
- Do not add undocumented configuration settings. Every setting appears in
  `config/waloader.example.toml` and `docs/configuration.md` per the goal file's
  documentation rules.
- Machine-specific values (binary paths, uv config path, hosts) belong in each
  machine's local, git-ignored `config/waloader.toml` — never hardcoded in code, tests,
  or committed config.

## Portability contract (highest-priority constraint)

Development/testing happens on **macOS and Windows 11**; production is **Red Hat
Enterprise Linux (~8.10)**. The same codebase must run on all three with configuration
changes only — no code edits.

- Use `pathlib.Path` everywhere; never hardcode path separators; use `os.pathsep` when
  composing env vars like `PYTHONPATH`.
- Subprocesses: always pass argv lists, never `shell=True` with command strings; no
  shell-specific syntax inside Python code.
- Detach child app processes so they survive WALoader restarts: `start_new_session=True`
  on POSIX; `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` creation flags on Windows.
  Use `psutil` for cross-platform process inspection/termination.
- Do not assume root/admin privileges, systemd, cron, Docker, Nginx, global package
  installation, or that port 80 is available.
- All external executables (Python, uv, Caddy) are configurable paths; defaults resolve
  via `shutil.which` on `PATH`.
- Where docs show shell commands, provide both: bash/zsh (macOS/Linux) first, then
  PowerShell (Windows).
- Timestamps stored in UTC ISO 8601.

## Stack

Python 3.12 · uv · Streamlit (>=1.37) · pandas · duckdb · plotly · sqlite3 (stdlib) ·
pyarrow · openpyxl · xlrd (for legacy `.xls`) · pydantic (config/validation) · psutil ·
rich · argon2-cffi · packaging (dependency parsing) · pytest · ruff.

Avoid unless the goal file explicitly requires it: FastAPI, Celery, Redis, Docker as a
hard dependency, Kubernetes, per-app OS users, mandatory systemd/cron, browser-based UI
test automation.

## Testing and validation

- Every phase adds/updates tests for the service-layer behavior it implements — tests
  are not deferred to the end.
- **Human-flow tests are mandatory, not optional.** Service-layer tests are necessary
  but not sufficient: they pass while real screens stay broken (this happened — a
  dialog closed on click, a toggle did nothing, yet every unit test was green). For
  every user-facing flow there MUST be a test that reproduces what a human actually
  does — navigate to the screen, click the button, submit the form, open the dialog —
  and asserts what a human expects to see at each step (the confirmation appears, the
  success/error message shows, the next screen is correct, the disabled state is
  right). Use Streamlit's `AppTest` to drive pages and widgets. When `AppTest` cannot
  model an interaction (e.g. a dialog persisting across a fragment rerun), split the
  flow into the testable halves AND add a guard at the seam (extract the handler and
  assert its behavior, e.g. that it uses a fragment-scoped rerun), and verify the
  whole flow once in a real browser. A feature is not done until its human flow has
  such a test. If a usability bug is found in the field, first add the failing
  human-flow test that reproduces it, then fix it.
- The app must *behave* and offer a flow that makes sense; the tests exist to
  guarantee that, not merely to exercise functions.
- Default suite must pass **offline**. Mark network/binary-dependent tests:
  `integration` (needs uv/network), `e2e` (real deployment), `caddy` (needs a Caddy
  binary); they must self-skip with a clear reason when prerequisites are missing.
- Standard validation: `uv run pytest` and `uv run ruff check .`. If ruff blocks
  progress for purely stylistic reasons, prioritize passing tests and working behavior,
  then document the issue in `DEVLOG.md`.

## Definition of done

Defined per-goal in the active goal file. A phase is done only when its acceptance
criteria are met, tests pass, validation was run, `PROGRESS.md` is updated, and
`DEVLOG.md` is appended. The goal is done only when its full Definition of Done
checklist passes.

## Environment profiles (reference values — machine specifics go in local config)

**Home macOS (current dev machine):** `uv` on PATH (`~/.local/bin/uv`); Python 3.12 at
`/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12`; Caddy v2.11.4 at
`/opt/homebrew/bin/caddy` (Homebrew, on PATH); public PyPI, no uv config file needed
(`system_certs` not required).

**Work Windows 11 (dev/test):** `uv` on PATH; PowerShell shell; corporate artifact
repository requires `system_certs = true`; uv config file at `C:\Users\bl81398\uv.toml`
(private index credentials — path may be referenced, contents must never be read or
logged); Python at
`C:\Users\bl81398\AppData\Local\Programs\Python\Python312\python.exe`; Caddy at
`C:\Users\bl81398\Downloads\caddy_windows_amd64.exe`.

**Work Red Hat (production):** paths set by operator in local config; no root; expect
a private package index via a uv config file; Caddy binary path configured explicitly.

## Canonical prompts

Kickoff / resume (same prompt works for both, in any agent):

> Read AGENTS.md, then implement goals/G01-waloader-complete.md completely. Work
> through its build phases in order, maintaining PROGRESS.md and DEVLOG.md at every
> phase boundary. Do not stop at scaffolding, analysis, or partial phases — continue
> until every item in the goal's Definition of Done passes, including the full test
> suite, ruff, the doctor command, and the end-to-end deployment check on this
> machine. If a previous session already made progress, resume from PROGRESS.md
> instead of restarting. Never touch the repo-local private/ directory and never read
> the contents of any uv config file.
