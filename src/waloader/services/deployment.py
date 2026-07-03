"""The deployment pipeline (G01 §4.7) — one composable flow reused by
create, retry-create, update, and retry-update.

Swap safety (§3.7): everything that can fail cheaply (parse, policy, venv,
install, app tests) happens BEFORE the running old version is touched. Only
then is the old process stopped and the new one launched on the same port.
A pre-swap failure leaves the old version running.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field

from packaging.requirements import Requirement

from waloader.config import WALoaderConfig
from waloader.logging_setup import app_log_dir
from waloader.models import App, AppVersion, User
from waloader.paths import ensure_dir
from waloader.repositories import approvals as approvals_repo
from waloader.repositories import apps as apps_repo
from waloader.repositories import audit as audit_repo
from waloader.repositories import deployments as deployments_repo
from waloader.repositories import notifications as notif_repo
from waloader.repositories import runtime as runtime_repo
from waloader.services import (
    bundles,
    caddy,
    dependency_policy,
    health,
    layout,
    ports,
    processes,
    slugs,
    states,
    uv_env,
    versioning,
)
from waloader.util import utc_now_iso

log = logging.getLogger(__name__)

INSTALL_TIMEOUT_SECONDS = 600


@dataclass
class DeployStep:
    name: str
    ok: bool
    output: str = ""


@dataclass
class DeployResult:
    ok: bool
    app_id: int
    kind: str
    version_number: int | None = None
    url: str | None = None
    error_summary: str | None = None
    steps: list[DeployStep] = field(default_factory=list)

    def error_block(self) -> str:
        """Copyable block concatenating every step's output (redacted upstream)."""
        parts = []
        for step in self.steps:
            marker = "OK " if step.ok else "FAIL"
            parts.append(f"[{marker}] {step.name}")
            if step.output.strip():
                parts.append(step.output.strip())
        if self.error_summary:
            parts.append(f"\nSUMMARY: {self.error_summary}")
        return "\n".join(parts)


def _run(command: list[str], env: dict[str, str], timeout: int, cwd=None):
    return subprocess.run(  # noqa: S603 - argv list, no shell
        command, env=env, capture_output=True, text=True, timeout=timeout,
        cwd=str(cwd) if cwd else None,
    )


def _requirement_names(requirements: list[str]) -> set[str]:
    names = set()
    for requirement in requirements:
        try:
            names.add(Requirement(requirement).name.lower())
        except Exception:  # invalid ones are rejected by policy before this
            pass
    return names


def _detect_tests(parsed: bundles.ParsedBundle) -> bool:
    for path in parsed.file_paths():
        name = path.rsplit("/", 1)[-1]
        if path.startswith("tests/") and name.endswith(".py"):
            return True
        if name.startswith("test_") and name.endswith(".py"):
            return True
        if name.endswith("_test.py"):
            return True
    return False


def _cleanup_old_venvs(config: WALoaderConfig, slug: str, keep_version: int) -> None:
    root = layout.venvs_root(config, slug)
    if not root.exists():
        return
    keep = layout.version_dirname(keep_version)
    for entry in root.iterdir():
        if entry.is_dir() and entry.name != keep:
            shutil.rmtree(entry, ignore_errors=True)


def deploy_bundle(
    conn: sqlite3.Connection,
    config: WALoaderConfig,
    app: App,
    bundle_bytes: bytes,
    *,
    actor_id: int | None,
    kind: str,
    _uv_runner=_run,
    _test_runner=_run,
    _launcher=processes.start_app,
    _prober=health.probe_app,
) -> DeployResult:
    result = DeployResult(ok=False, app_id=app.id, kind=kind)
    deployment = deployments_repo.start(conn, app_id=app.id, kind=kind)
    conn.commit()

    was_running = processes.is_app_running(conn, app)
    swapped = False
    version: AppVersion | None = None
    launched: tuple[int, float] | None = None
    app = states.transition(conn, app, states.DEPLOYING)

    def step(name: str, ok: bool, output: str = "") -> None:
        result.steps.append(DeployStep(name, ok, uv_env.redact(output)))

    def finalize(ok: bool, error_summary: str | None = None) -> DeployResult:
        nonlocal app
        result.ok = ok
        result.error_summary = error_summary
        if version is not None:
            log_dir = app_log_dir(config, app.slug, version.version_number)
            (log_dir / "deploy.log").write_text(
                f"deployment kind={kind} at={utc_now_iso()} ok={ok}\n\n"
                + result.error_block() + "\n",
                encoding="utf-8",
            )
        if ok:
            deployments_repo.finish(
                conn, deployment.id, status="succeeded",
                version_id=version.id if version else None,
            )
            apps_repo.set_last_deploy_error(conn, app.id, None)
        else:
            deployments_repo.finish(
                conn, deployment.id, status="failed", error_summary=error_summary,
                version_id=version.id if version else None,
            )
            apps_repo.set_last_deploy_error(conn, app.id, result.error_block())
            if launched is not None:
                processes.terminate_tree(
                    launched[0], launched[1], timeout=config.apps.stop_timeout_seconds
                )
                runtime_repo.clear_process(conn, app.id)
            if was_running and not swapped:
                app = states.transition(conn, app, states.RUNNING)  # old version untouched
            else:
                app = states.transition(conn, app, states.DEPLOYMENT_FAILED)
        audit_repo.record(
            conn, actor=str(actor_id or ""), action=f"deploy.{kind}", target=app.slug,
            details={"ok": ok, "version": version.version_number if version else None},
        )
        conn.commit()
        return result

    # 1. parse + validate bundle -----------------------------------------
    try:
        parsed = bundles.parse_bundle_bytes(
            bundle_bytes,
            max_mb=config.uploads.max_markdown_bundle_mb,
            max_files=config.uploads.max_bundle_files,
        )
        step("parse bundle", True,
             f"{len(parsed.files)} files, entrypoint {parsed.entrypoint}"
             + ("".join(f"\nwarning: {w}" for w in parsed.warnings)))
    except bundles.BundleError as exc:
        step("parse bundle", False, str(exc))
        return finalize(False, f"Bundle validation failed: {exc}")

    # 2. reconstruct into a new version ----------------------------------
    version = versioning.create_version(
        conn, config, app, parsed, bundle_bytes, created_by=actor_id
    )
    step("create version", True, f"version {version.version_number:06d}")

    # 3. dependency policy -------------------------------------------------
    pyproject = next((f for f in parsed.files if f.path == "pyproject.toml"), None)
    if pyproject is not None:
        try:
            declared = dependency_policy.extract_dependencies(pyproject.content)
        except dependency_policy.PyprojectError as exc:
            step("dependency policy", False, str(exc))
            return finalize(False, str(exc))
        policy_result = dependency_policy.validate_dependencies(
            declared,
            config.dependencies_policy,
            base_dependencies=config.apps.base_dependencies,
            approved=set(approvals_repo.list_for_app(conn, app.id)),
        )
        if not policy_result.ok:
            block = dependency_policy.format_violations(policy_result)
            step("dependency policy", False, block)
            summary = (
                "Dependencies need admin approval"
                if policy_result.needs_approval and not policy_result.violations
                else "Dependency policy violations"
            )
            return finalize(False, summary)
        install_requirements = list(policy_result.allowed)
        step("dependency policy", True, f"{len(install_requirements)} requirements allowed")
    else:
        install_requirements = list(config.apps.base_dependencies)
        step("dependency policy", True, "no pyproject.toml; using the approved base set")

    names = _requirement_names(install_requirements)
    if "streamlit" not in names:
        install_requirements.append("streamlit")
    for sdk_requirement in config.apps.sdk_dependencies:
        if Requirement(sdk_requirement).name.lower() not in names:
            install_requirements.append(sdk_requirement)

    # 4. per-version venv + install ---------------------------------------
    venv_path = layout.venv_dir(config, app.slug, version.version_number)
    venv_python = uv_env.venv_python(venv_path)
    env = uv_env.build_env(config)
    try:
        create_cmd = uv_env.uv_command(
            config, "venv", str(venv_path), python=config.resolved_python()
        )
        created = _uv_runner(create_cmd, env, INSTALL_TIMEOUT_SECONDS)
        if created.returncode != 0:
            step("create venv", False, created.stdout + "\n" + created.stderr)
            return finalize(False, "Could not create the app's virtual environment")
        step("create venv", True)

        install_cmd = uv_env.uv_command(
            config, "pip", "install", *install_requirements, python=venv_python
        )
        installed = _uv_runner(install_cmd, env, INSTALL_TIMEOUT_SECONDS)
        if installed.returncode != 0:
            step("install dependencies", False, installed.stdout + "\n" + installed.stderr)
            return finalize(False, "Dependency installation failed")
        step("install dependencies", True, ", ".join(install_requirements))
    except uv_env.UvNotFoundError as exc:
        step("install dependencies", False, str(exc))
        return finalize(False, str(exc))
    except subprocess.TimeoutExpired:
        step("install dependencies", False,
             f"timed out after {INSTALL_TIMEOUT_SECONDS}s")
        return finalize(False, "Dependency installation timed out")

    # 5. run the app's own tests if present --------------------------------
    source = layout.resolve(config, version.source_path)
    if _detect_tests(parsed):
        try:
            pytest_install = _uv_runner(
                uv_env.uv_command(config, "pip", "install", "pytest", python=venv_python),
                env, INSTALL_TIMEOUT_SECONDS,
            )
            if pytest_install.returncode != 0:
                step("app tests", False, pytest_install.stdout + "\n" + pytest_install.stderr)
                return finalize(False, "Could not install pytest for the app's tests")
            tested = _test_runner(
                [str(venv_python), "-m", "pytest", "-q"],
                dict(env), config.apps.test_timeout_seconds, source,
            )
            test_output = (tested.stdout + "\n" + tested.stderr).strip()
            log_dir = app_log_dir(config, app.slug, version.version_number)
            (log_dir / "test.log").write_text(test_output + "\n", encoding="utf-8")
            if tested.returncode != 0:
                step("app tests", False, test_output)
                return finalize(False, "The app's own tests failed")
            step("app tests", True, test_output.splitlines()[-1] if test_output else "")
        except subprocess.TimeoutExpired:
            step("app tests", False,
                 f"timed out after {config.apps.test_timeout_seconds}s")
            return finalize(False, "The app's tests timed out")
    else:
        step("app tests", True, "no tests in bundle")

    # 6. port ---------------------------------------------------------------
    try:
        port = ports.allocate_port(conn, config, app.id)
        app = apps_repo.get(conn, app.id)
        step("allocate port", True, str(port))
    except ports.PortAllocationError as exc:
        step("allocate port", False, str(exc))
        return finalize(False, str(exc))

    # 7. swap: stop old, launch new ------------------------------------------
    if was_running:
        processes.stop_app(conn, config, app)
        step("stop previous version", True)
    swapped = True
    try:
        launched = _launcher(conn, config, app, version)
        step("launch app", True, f"pid {launched[0]}")
    except Exception as exc:  # launch errors surface with full detail
        step("launch app", False, str(exc))
        return finalize(False, f"Could not launch the app: {exc}")

    # 8. caddy routes (never fails the deployment) -----------------------------
    if config.caddy.enabled:
        route = f"/apps/{app.slug}"
        apps_repo.set_caddy_route(conn, app.id, route)
        refreshed = caddy.refresh_routes(conn, config)
        step("update caddy", refreshed.ok,
             refreshed.output if not refreshed.ok else route)
    else:
        apps_repo.set_caddy_route(conn, app.id, None)

    # 9. initial health check ---------------------------------------------------
    deadline = time.monotonic() + config.health.initial_check_timeout_seconds
    probe = None
    while time.monotonic() < deadline:
        alive = processes.pid_matches(*launched)
        probe = _prober(config, app, process_alive=alive)
        if probe.healthy:
            break
        if not alive:
            break
        time.sleep(0.5)
    if probe is None or not probe.healthy:
        runtime_log = app_log_dir(
            config, app.slug, version.version_number
        ) / "runtime.log"
        tail = processes.tail_log(runtime_log, lines=80)
        reason = probe.reason if probe else "no health probe ran"
        step("initial health check", False, f"{reason}\n--- runtime.log tail ---\n{tail}")
        return finalize(False, f"The app did not become healthy: {reason}")
    step("initial health check", True)

    # success ---------------------------------------------------------------
    apps_repo.set_current_version(conn, app.id, version.version_number)
    runtime_repo.set_deployed_healthy(conn, app.id, True)
    notif_repo.clear_for_app(conn, app.id)  # a fresh deploy resets crash dedupe
    app = states.transition(conn, app, states.RUNNING)
    _cleanup_old_venvs(config, app.slug, version.version_number)
    result.version_number = version.version_number
    result.url = health.app_url(config, app)
    return finalize(True)


# --- orchestration -----------------------------------------------------------


class AppCreationError(ValueError):
    pass


def create_app_and_deploy(
    conn: sqlite3.Connection,
    config: WALoaderConfig,
    *,
    owner: User,
    name: str,
    description: str,
    user_mgmt_enabled: bool,
    bundle_bytes: bytes,
    **test_seams,
) -> tuple[App, DeployResult]:
    availability = slugs.check_name_available(conn, name)
    if not availability.available:
        raise AppCreationError(availability.reason)
    app = apps_repo.create(
        conn,
        owner_id=owner.id,
        name=name.strip(),
        slug=availability.slug,
        description=description,
        user_mgmt_enabled=user_mgmt_enabled,
    )
    ensure_dir(layout.app_dir(config, app.slug))
    audit_repo.record(conn, actor=owner.username, action="app.create", target=app.slug)
    conn.commit()
    result = deploy_bundle(
        conn, config, app, bundle_bytes, actor_id=owner.id, kind="create", **test_seams
    )
    return apps_repo.get(conn, app.id), result


def redeploy(
    conn: sqlite3.Connection,
    config: WALoaderConfig,
    app: App,
    bundle_bytes: bytes,
    *,
    actor_id: int | None,
    **test_seams,
) -> DeployResult:
    """Update / retry — kind derived from the app's history."""
    if app.current_version is None:
        kind = "retry-create"
    elif app.state == states.DEPLOYMENT_FAILED:
        kind = "retry-update"
    else:
        kind = "update"
    return deploy_bundle(
        conn, config, app, bundle_bytes, actor_id=actor_id, kind=kind, **test_seams
    )
