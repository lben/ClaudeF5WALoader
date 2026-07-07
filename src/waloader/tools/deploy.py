"""Update a remote WALoader install safely (no git needed on the server).

Three subcommands:

    package   build a deployable tarball from git-tracked files (runs here)
    apply     apply a tarball to an install, preserving runtime state (runs
              on the server; stdlib-only, Python 3.6+ so stock RHEL python3
              can run it standalone)
    push      package + scp + remote apply over your existing ssh/scp — the
              one-command update

Why this is safe to run over a live, configured server:

- The payload is exactly the **git-tracked files** (`git ls-files`). Your
  ``data/``, ``config/waloader.toml`` and ``.venv`` are git-ignored, so they
  are never in the payload and never overwritten.
- A hardcoded PROTECTED denylist is refused for both overwrite and deletion,
  even if something was force-added to git.
- Files removed between versions are cleaned up via a manifest diff (naive
  unzip can't do this) — but never protected paths.
- ``apply`` refuses a target that is not already a WALoader install, and
  aborts if the payload is missing sentinel files (guards against wiping the
  server with an empty/garbage tarball).

Deliberately stdlib-only and dependency-free (no Fabric/paramiko): it shells
out to your system ``ssh``/``scp``, so whatever auth already works for you
(password prompt or key) keeps working.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

# Never overwritten, never deleted, never packaged — even if force-added to git.
PROTECTED_FILES = ("config/waloader.toml", "config/deploy.toml")
PROTECTED_TOP_DIRS = ("data", "private", ".venv", ".git", ".deploy")
SENTINELS = ("pyproject.toml", "src/waloader/__init__.py")
MANIFEST_IN_ARCHIVE = "MANIFEST.json"
LOCAL_MANIFEST = ".deploy/manifest.json"

_WALK_SKIP_DIRS = {
    ".git", "data", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache",
    ".deploy", "node_modules", ".mypy_cache",
}


class DeployError(Exception):
    pass


def is_protected(rel: str) -> bool:
    rel = rel.replace("\\", "/").strip("/")
    if rel in PROTECTED_FILES:
        return True
    first = rel.split("/", 1)[0]
    return first in PROTECTED_TOP_DIRS


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(str(path), "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --- payload selection (source side) ---------------------------------------


def compute_payload_files(root: Path, use_git: bool = True) -> list:
    """Relative POSIX paths that make up the project, excluding runtime state."""
    if use_git:
        try:
            out = subprocess.run(
                ["git", "-C", str(root), "ls-files", "-z"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True,
            )
        except FileNotFoundError as exc:
            raise DeployError("git not found; re-run with --no-git") from exc
        if out.returncode != 0:
            raise DeployError(
                "git ls-files failed (is {0} a git repo? use --no-git): {1}".format(
                    root, out.stderr.strip()
                )
            )
        candidates = [p for p in out.stdout.split("\0") if p]
    else:
        candidates = []
        root_resolved = root.resolve()
        for dirpath, dirnames, filenames in os.walk(str(root)):
            dirnames[:] = [d for d in dirnames if d not in _WALK_SKIP_DIRS]
            for name in filenames:
                full = Path(dirpath) / name
                rel = full.resolve().relative_to(root_resolved).as_posix()
                candidates.append(rel)

    files = []
    for rel in sorted(set(candidates)):
        if is_protected(rel):
            continue
        if rel.endswith((".pyc", ".pyo")) or rel.endswith(".DS_Store"):
            continue
        if not (root / rel).is_file():
            continue  # tracked-but-deleted; skip with no fuss
        files.append(rel)
    return files


def build_manifest(root: Path, files: list) -> dict:
    version = ""
    init = root / "src" / "waloader" / "__init__.py"
    if init.is_file():
        for line in init.read_text(encoding="utf-8").splitlines():
            if line.startswith("__version__"):
                version = line.split("=", 1)[1].strip().strip("\"'")
                break
    return {
        "waloader_version": version,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": {rel: _sha256(root / rel) for rel in files},
    }


def plan_deletions(old_manifest: dict, new_manifest: dict) -> list:
    """Files present in the previous deploy but not this one — minus protected."""
    old_files = set((old_manifest or {}).get("files", {}))
    new_files = set((new_manifest or {}).get("files", {}))
    return sorted(
        rel for rel in (old_files - new_files) if not is_protected(rel)
    )


def create_package(root: Path, out_path: Path, use_git: bool = True) -> dict:
    files = compute_payload_files(root, use_git=use_git)
    if not files:
        raise DeployError("payload is empty — nothing to deploy")
    manifest = build_manifest(root, files)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(str(out_path), "w:gz") as tar:
        for rel in files:
            tar.add(str(root / rel), arcname=rel)
        data = json.dumps(manifest, indent=2).encode("utf-8")
        info = tarfile.TarInfo(MANIFEST_IN_ARCHIVE)
        info.size = len(data)
        info.mtime = int(time.time())
        import io

        tar.addfile(info, io.BytesIO(data))
    return manifest


# --- apply (server side, stdlib-only, 3.6+) --------------------------------


def _safe_extract(tarball: Path, dest: Path) -> None:
    dest_resolved = dest.resolve()
    with tarfile.open(str(tarball), "r:*") as tar:
        for member in tar.getmembers():
            target = (dest / member.name).resolve()
            if dest_resolved != target and dest_resolved not in target.parents:
                raise DeployError(
                    "tarball member escapes staging: {0}".format(member.name)
                )
        # Python 3.12+ warns without an explicit filter; 'data' matches our own
        # containment check. Older pythons (RHEL 3.6) lack the kwarg — fall back.
        try:
            tar.extractall(str(dest), filter="data")
        except TypeError:
            tar.extractall(str(dest))


def safe_target(root: Path, rel: str) -> Path:
    if is_protected(rel):
        raise DeployError("refusing to touch protected path: {0}".format(rel))
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise DeployError("path escapes the install root: {0}".format(rel))
    return target


def _read_manifest_file(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def apply_package(
    root: Path,
    tarball: Path,
    *,
    uv: str = "uv",
    dry_run: bool = False,
    run_uv: bool = True,
    run_migrate: bool = True,
    restart: bool = True,
) -> dict:
    root = root.resolve()
    if not (root / "pyproject.toml").is_file():
        raise DeployError(
            "{0} does not look like a WALoader install (no pyproject.toml). "
            "Refusing to apply.".format(root)
        )

    staging = Path(tempfile.mkdtemp(prefix="waloader-deploy-"))
    try:
        _safe_extract(tarball, staging)
        new_manifest = _read_manifest_file(staging / MANIFEST_IN_ARCHIVE)
        new_files = new_manifest.get("files", {})
        missing_sentinels = [s for s in SENTINELS if s not in new_files]
        if missing_sentinels:
            raise DeployError(
                "payload is missing sentinel files {0} — aborting to avoid "
                "damaging the install".format(missing_sentinels)
            )

        old_manifest = _read_manifest_file(root / LOCAL_MANIFEST)
        deletions = plan_deletions(old_manifest, new_manifest)

        # classify adds/updates vs unchanged (by hash) for the report
        updates = []
        for rel, digest in new_files.items():
            target = root / rel
            if not target.is_file() or _sha256(target) != digest:
                updates.append(rel)

        report = {
            "updates": sorted(updates),
            "deletions": deletions,
            "unchanged": len(new_files) - len(updates),
            "dry_run": dry_run,
        }
        if dry_run:
            return report

        # 1. write/overwrite payload files atomically
        for rel in new_files:
            source = staging / rel
            target = safe_target(root, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.parent / (target.name + ".deploy-tmp")
            shutil.copy2(str(source), str(tmp))
            os.replace(str(tmp), str(target))

        # 2. remove files that vanished between versions (never protected)
        for rel in deletions:
            target = safe_target(root, rel)
            if target.is_file():
                target.unlink()
                _prune_empty_dirs(target.parent, root)

        # 3. record the new manifest for the next diff
        manifest_path = root / LOCAL_MANIFEST
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(new_manifest, indent=2), encoding="utf-8"
        )
    finally:
        shutil.rmtree(str(staging), ignore_errors=True)

    # 4. dependencies, migrations, restart
    report["steps"] = _finalize(root, uv, run_uv, run_migrate, restart)
    return report


def _prune_empty_dirs(directory: Path, root: Path) -> None:
    root = root.resolve()
    current = directory.resolve()
    while current != root and root in current.parents:
        try:
            if any(current.iterdir()):
                return
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _run(cmd: list, cwd: Path) -> tuple:
    proc = subprocess.run(
        cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        universal_newlines=True,
    )
    return proc.returncode, (proc.stdout or "").strip()


def _finalize(root: Path, uv: str, run_uv: bool, run_migrate: bool,
              restart: bool) -> list:
    steps = []

    def do(label, cmd):
        rc, out = _run(cmd, root)
        steps.append({"step": label, "ok": rc == 0, "output": out})
        return rc == 0

    if restart:
        do("stop daemon", [uv, "run", "python", "-m", "waloader.tools.serve",
                           "--stop"])
    if run_uv:
        if not do("uv sync", [uv, "sync"]):
            steps.append({"step": "ABORTED", "ok": False,
                          "output": "uv sync failed — not restarting"})
            return steps
    if run_migrate:
        do("db migrate", [uv, "run", "python", "-m", "waloader.tools.db",
                          "migrate"])
    if restart:
        do("start daemon", [uv, "run", "python", "-m", "waloader.tools.serve",
                            "--daemon"])
    return steps


# --- push (source side) ----------------------------------------------------


def _load_deploy_conf(root: Path, path: Path | None) -> dict:
    conf_path = path or (root / "config" / "deploy.toml")
    if not conf_path.is_file():
        return {}
    import tomllib  # 3.11+, source machine only

    return tomllib.loads(conf_path.read_text(encoding="utf-8")).get("remote", {})


def _ssh_opts(conn: dict) -> list:
    opts = []
    if conn.get("identity_file"):
        opts += ["-i", os.path.expanduser(str(conn["identity_file"]))]
    if conn.get("port"):
        opts += ["-p", str(conn["port"])]  # scp uses -P; handled separately
    return opts


def _scp_opts(conn: dict) -> list:
    opts = []
    if conn.get("identity_file"):
        opts += ["-i", os.path.expanduser(str(conn["identity_file"]))]
    if conn.get("port"):
        opts += ["-P", str(conn["port"])]  # NOTE: capital P for scp
    return opts


def _dest(conn: dict) -> str:
    host = conn["host"]
    user = conn.get("user")
    return "{0}@{1}".format(user, host) if user else host


def _require_binary(name: str, label: str) -> None:
    """Fail early and helpfully if ssh/scp can't be launched (the WinError 2
    the raw traceback showed)."""
    if os.path.isabs(name) or os.sep in name or (os.altsep and os.altsep in name):
        if not Path(name).exists():
            raise DeployError("{0} binary not found at: {1}".format(label, name))
        return
    if shutil.which(name) is None:
        raise DeployError(
            "'{0}' was not found on PATH, so push cannot reach the server.\n"
            "Fixes:\n"
            "  - Windows: install the OpenSSH client (Settings > Apps > Optional "
            "Features > OpenSSH Client), then reopen your terminal; or\n"
            "  - point deploy at an existing client, e.g. Git for Windows, by "
            "adding to config/deploy.toml:\n"
            "        ssh = \"C:/Program Files/Git/usr/bin/ssh.exe\"\n"
            "        scp = \"C:/Program Files/Git/usr/bin/scp.exe\"\n"
            "  - or skip push entirely and use the manual package + apply flow "
            "(see docs/deploying-updates.md).".format(name)
        )


def _run_or_raise(cmd: list, label: str, cwd: Path | None = None,
                  check: bool = True) -> int:
    try:
        rc = subprocess.run(cmd, cwd=str(cwd) if cwd else None).returncode
    except OSError as exc:
        raise DeployError(
            "could not run {0} ({1}): {2}".format(label, cmd[0], exc)
        ) from exc
    if check and rc != 0:
        raise DeployError("{0} failed (exit {1})".format(label, rc))
    return rc


def push(root: Path, conn: dict, *, use_git: bool = True, restart: bool = True,
         run_migrate: bool = True, dry_run: bool = False,
         remote_python: str = "python3") -> int:
    for required in ("host", "remote_dir"):
        if not conn.get(required):
            raise DeployError(
                "missing '{0}' — set it in config/deploy.toml or pass "
                "--{0}".format(required)
            )
    remote_dir = str(conn["remote_dir"]).rstrip("/")
    uv = conn.get("uv", "uv")
    dest = _dest(conn)
    ssh_bin = conn.get("ssh") or "ssh"
    scp_bin = conn.get("scp") or "scp"
    ssh_opts = _ssh_opts(conn)
    scp_opts = _scp_opts(conn)
    staging = remote_dir + "/.deploy/incoming"

    # remote commands are quoted for the REMOTE shell (bash on the server).
    # apply is wrapped in `bash -lc` so the login PATH finds uv.
    apply_inner = " ".join([
        shlex.quote(remote_python),
        shlex.quote(staging + "/deploy.py"), "apply",
        shlex.quote(staging + "/payload.tar.gz"),
        "--root", shlex.quote(remote_dir),
        "--uv", shlex.quote(uv),
        "--restart" if restart else "--no-restart",
    ] + ([] if run_migrate else ["--no-migrate"]))
    remote_mkdir = "mkdir -p " + shlex.quote(staging)
    remote_apply = "bash -lc " + shlex.quote(apply_inner)

    tmp = Path(tempfile.mkdtemp(prefix="waloader-push-"))
    try:
        tarball = tmp / "payload.tar.gz"
        manifest = create_package(root, tarball, use_git=use_git)
        print("packaged {0} files (waloader {1})".format(
            len(manifest["files"]), manifest["waloader_version"] or "?"))

        if dry_run:
            print("[dry-run] upload to:   {0}:{1}/".format(dest, staging))
            print("[dry-run] remote apply: {0}".format(apply_inner))
            return 0

        _require_binary(ssh_bin, "ssh")
        _require_binary(scp_bin, "scp")

        # copy the apply script next to the tarball and scp with cwd=tmp using
        # BARE filenames — passing a Windows path like C:\...\payload.tar.gz to
        # scp makes it read `C:` as a hostname. Bare names sidestep that.
        shutil.copy2(str(Path(__file__).resolve()), str(tmp / "deploy.py"))

        _run_or_raise([ssh_bin] + ssh_opts + [dest, remote_mkdir],
                      "ssh (mkdir)")
        _run_or_raise(
            [scp_bin] + scp_opts + ["payload.tar.gz", "deploy.py",
                                    dest + ":" + staging + "/"],
            "scp (upload)", cwd=tmp,
        )
        print("uploaded; applying on {0} …".format(conn["host"]))
        return _run_or_raise([ssh_bin] + ssh_opts + [dest, remote_apply],
                             "remote apply", check=False)
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


# --- CLI -------------------------------------------------------------------


def _print_apply_report(report: dict) -> None:
    print("updates:   {0}".format(len(report["updates"])))
    print("unchanged: {0}".format(report["unchanged"]))
    print("deletions: {0}".format(len(report["deletions"])))
    for rel in report["deletions"]:
        print("  - {0}".format(rel))
    if report.get("dry_run"):
        print("(dry run — nothing changed)")
    for step in report.get("steps", []):
        mark = "ok" if step["ok"] else "FAILED"
        print("[{0}] {1}".format(mark, step["step"]))
        if not step["ok"] and step["output"]:
            print("    " + step["output"].replace("\n", "\n    "))


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="waloader.tools.deploy",
        description="Safely update a remote WALoader install",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    pkg = sub.add_parser("package", help="build a deployable tarball")
    pkg.add_argument("--root", default=".")
    pkg.add_argument("--out", required=True)
    pkg.add_argument("--no-git", action="store_true")

    app = sub.add_parser("apply", help="apply a tarball to an install (server)")
    app.add_argument("tarball")
    app.add_argument("--root", default=".")
    app.add_argument("--uv", default="uv")
    app.add_argument("--dry-run", action="store_true")
    app.add_argument("--no-uv", action="store_true")
    app.add_argument("--no-migrate", action="store_true")
    restart_group = app.add_mutually_exclusive_group()
    restart_group.add_argument("--restart", dest="restart", action="store_true",
                               default=True)
    restart_group.add_argument("--no-restart", dest="restart",
                               action="store_false")

    psh = sub.add_parser("push", help="package + scp + remote apply")
    psh.add_argument("--root", default=".")
    psh.add_argument("--config", default=None,
                     help="deploy config (default: config/deploy.toml)")
    psh.add_argument("--host")
    psh.add_argument("--user")
    psh.add_argument("--port")
    psh.add_argument("--identity")
    psh.add_argument("--remote-dir")
    psh.add_argument("--uv")
    psh.add_argument("--ssh", help="ssh binary (default: ssh on PATH)")
    psh.add_argument("--scp", help="scp binary (default: scp on PATH)")
    psh.add_argument("--remote-python", default="python3")
    psh.add_argument("--no-git", action="store_true")
    psh.add_argument("--no-migrate", action="store_true")
    psh.add_argument("--dry-run", action="store_true")
    restart_p = psh.add_mutually_exclusive_group()
    restart_p.add_argument("--restart", dest="restart", action="store_true",
                           default=True)
    restart_p.add_argument("--no-restart", dest="restart", action="store_false")

    args = parser.parse_args(argv)
    root = Path(args.root)

    try:
        if args.command == "package":
            manifest = create_package(root, Path(args.out),
                                      use_git=not args.no_git)
            print("packaged {0} files -> {1}".format(
                len(manifest["files"]), args.out))
            return 0

        if args.command == "apply":
            report = apply_package(
                root, Path(args.tarball), uv=args.uv, dry_run=args.dry_run,
                run_uv=not args.no_uv, run_migrate=not args.no_migrate,
                restart=args.restart,
            )
            _print_apply_report(report)
            return 0

        if args.command == "push":
            conn = _load_deploy_conf(
                root, Path(args.config) if args.config else None
            )
            for key, value in (
                ("host", args.host), ("user", args.user), ("port", args.port),
                ("identity_file", args.identity), ("remote_dir", args.remote_dir),
                ("uv", args.uv), ("ssh", args.ssh), ("scp", args.scp),
            ):
                if value:
                    conn[key] = value
            return push(
                root, conn, use_git=not args.no_git, restart=args.restart,
                run_migrate=not args.no_migrate, dry_run=args.dry_run,
                remote_python=args.remote_python,
            )
    except DeployError as exc:
        print("deploy error: {0}".format(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
