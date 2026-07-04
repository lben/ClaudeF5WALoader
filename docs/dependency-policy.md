# Dependency policy

Child apps declare dependencies in their bundle's `pyproject.toml` under
`[project.dependencies]` (PEP 508 strings). At deployment, WALoader validates
every requirement against the policy, then installs the allowed list into the
app's **own per-version virtualenv** (shared uv cache, never a global venv).
No `pyproject.toml` → the approved base set is installed instead
(`apps.base_dependencies`: streamlit, pandas, plotly, duckdb, pyarrow).
`streamlit` and the SDK runtime deps (`apps.sdk_dependencies`, argon2-cffi)
are always ensured.

## The five switches (`[dependencies_policy]`)

| Setting | Default | true means | false means |
|---|---|---|---|
| `allow_app_dependencies` | `true` | apps may declare normal PyPI packages | only the approved base set is allowed |
| `allow_direct_url_dependencies` | `false` | `pkg @ https://…/x.whl` allowed | rejected |
| `allow_vcs_dependencies` | `false` | `pkg @ git+https://…` allowed | rejected |
| `allow_path_dependencies` | `false` | `pkg @ file://…` allowed | rejected |
| `require_admin_approval_for_new_dependencies` | `false` | unapproved requirements block deployment until an admin approves them | installs are attempted automatically |

Violations abort the deployment with a copyable block naming each offending
requirement and the switch that blocked it, e.g.:

```text
REJECTED  pkg @ git+https://github.com/a/b
  reason: VCS dependencies are disabled (dependencies_policy.allow_vcs_dependencies = false)
```

## Approval mode

With `require_admin_approval_for_new_dependencies = true`, a deployment using
a not-yet-approved requirement stops in a clearly reported state; approvals
are stored per app + exact requirement string (`dependency_approvals` table).
After approval, the user retries the same bundle and it proceeds. Base-set
packages never need approval.

## uv, private indexes, and secrets

All resolution/installation uses uv with the operator-configured environment:
`UV_CONFIG_FILE` (path to a corporate uv.toml — **its contents are secret and
are never read, printed, or logged by WALoader**), `UV_CACHE_DIR` (shared
cache, default `data/uv-cache`), `UV_SYSTEM_CERTS`, optional
`SSL_CERT_FILE`/`SSL_CERT_DIR`, and `--allow-insecure-host` only when
explicitly configured. Credentials (`https://user:pass@…`, `?token=…`) are
redacted from every surfaced command line and error.

Connectivity check (also part of `doctor`):

bash/zsh:

```bash
uv run python -m waloader.tools.doctor          # includes the uv preflight
```

PowerShell: identical. The preflight resolves `uv.preflight_packages`
(default `["pandas"]`) with `uv pip install --dry-run` using exactly the
binary and environment deployments will use.
