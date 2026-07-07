# WALoader

A Streamlit-based internal platform that lets finance users deploy and manage
LLM-generated Streamlit apps **without being developers**: they upload one
structured markdown bundle; WALoader validates it, rebuilds the project,
installs its dependencies in an isolated per-version virtualenv, runs its
tests, deploys it on a managed port (optionally behind Caddy with clean URLs),
health-checks it, and provides datasets, per-app user management, backups, and
operator tooling.

Portable by contract: develop/test on macOS or Windows 11, deploy on Red Hat
Linux — configuration changes only.

## Quickstart

bash/zsh (macOS/Linux):

```bash
uv sync
cp config/waloader.example.toml config/waloader.toml   # edit for this machine
uv run python -m waloader.tools.doctor                 # environment self-check
uv run python -m waloader.tools.users create-admin you # or use the first-run screen
uv run python -m waloader.tools.serve                  # http://localhost:8501
```

PowerShell (Windows):

```powershell
uv sync
Copy-Item config\waloader.example.toml config\waloader.toml
uv run python -m waloader.tools.doctor
uv run python -m waloader.tools.users create-admin you
uv run python -m waloader.tools.serve
```

Then: **Create new app** → upload `examples/sample-bundle.md` → open the URL
it prints. Production runbook: `docs/troubleshooting.md`.

## The user flow

1. A finance user builds a Streamlit app with their coding LLM — which the
   operator has primed with the [`authoring_kit/`](authoring_kit/) so it is
   WALoader-native from the first message (design language, Dataset Concepts,
   tests, previews, bundle output).
2. They log into WALoader, hit *Create new app*, pick a name (live
   availability check), upload the bundle, submit.
3. Success → copyable app URL. Failure → copyable error block to paste back to
   the LLM, plus a retry-upload button. The same flow serves updates.
4. Data arrives via **Dataset Concepts** (`docs/dataset-concepts-contract.md`):
   named datasets the code loads with `load_dataset("clients")`, uploadable and
   replaceable (schema-diffed) without redeploys.

## Documentation

| Doc | What |
|---|---|
| `docs/configuration.md` | every setting: purpose, default, consequences |
| `docs/markdown-bundle-contract.md` | the bundle format, exactly |
| `authoring_kit/` | system prompt + guides that make a coding LLM WALoader-native (operator wires them in; see its README) |
| `docs/llm-bundle-prompt.md` | fallback prompt for un-primed LLMs (superseded by the kit) |
| `docs/dataset-concepts-contract.md` | datasets: SDK + mapping screen |
| `docs/user-management.md` | platform users, app users, crash emails |
| `docs/dependency-policy.md` | what child apps may depend on; uv/private index |
| `docs/process-management.md` | lifecycle, health checks, reconciliation |
| `docs/caddy-reverse-proxy.md` | clean URLs on one port |
| `docs/backups-and-restore.md` | scoped backups, restore, app export/import, factory reset, machine migration |
| `docs/deploying-updates.md` | safely updating a running server (no git needed on the box) |
| `docs/troubleshooting.md` | runbook incl. RHEL deployment |
| `docs/manual-smoke-checklist.md` | human verification pass |

## CLI tools

`python -m waloader.tools.<tool>`: `serve` (run the UI), `doctor`
(environment self-check), `db` (migrate/status/backup), `appctl`
(list/status/start/stop/restart/logs/health/reconcile/rebuild/export/import),
`caddyctl` (generate/validate/start/stop/reload/status), `backupctl`
(create scoped backups/list/restore/factory-reset), `maintenance`
(backup-db/cleanup-retention/cleanup-logs/…/run-all), `users`
(create-admin/list/reset-password), `deploy`
(package/apply/push — safely update a running server, `docs/deploying-updates.md`).
Most are thin wrappers over the same services the UI uses.

## Development

```bash
uv run pytest                      # offline unit suite
uv run pytest -m integration      # needs uv + network
uv run pytest -m e2e              # real deployment round-trip
uv run pytest -m caddy            # needs a caddy binary
uv run ruff check .
```

Contributor/agent contract: `AGENTS.md`. Spec of record:
`goals/G01-waloader-complete.md`. Status: `PROGRESS.md` / `DEVLOG.md`.

## Trust model (read this once)

Child apps are **trusted code** uploaded by authenticated internal users; they
run under the same OS account as WALoader. WALoader validates bundle *paths*
and dependency *policy* — it does not sandbox code behavior. Do not expose it
to untrusted uploaders.
