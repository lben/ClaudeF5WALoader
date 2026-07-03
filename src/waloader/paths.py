"""Filesystem/path utilities, including the protected ``private/`` guard.

The protected location is the repository-local ``private/`` directory and, for
relative paths inside uploaded bundles, anything at or under ``private/``.
The macOS system ``/private`` (backing /tmp, /var, /etc) is unrelated and is
deliberately NOT matched by these guards.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

PROTECTED_DIR_NAME = "private"


class ProtectedPathError(Exception):
    pass


class UnsafePathError(Exception):
    pass


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_protected_path(path: Path, root: Path) -> bool:
    """True if ``path`` is the repo-local ``<root>/private`` or inside it."""
    protected = (root / PROTECTED_DIR_NAME).resolve()
    resolved = path if path.is_absolute() else root / path
    resolved = resolved.resolve()
    return resolved == protected or protected in resolved.parents


def ensure_not_protected(path: Path, root: Path) -> Path:
    if is_protected_path(path, root):
        raise ProtectedPathError(f"Refusing to touch protected path: {path}")
    return path


def is_protected_relative(relative: str) -> bool:
    """For bundle-relative POSIX paths: 'private' itself or anything under it."""
    parts = PurePosixPath(relative).parts
    return bool(parts) and parts[0].lower() == PROTECTED_DIR_NAME


def safe_join(root: Path, relative: str) -> Path:
    """Join a relative path onto root, guaranteeing the result stays inside root.

    Rejects absolute paths, drive letters, backslashes, ``..`` and the protected
    ``private/`` prefix. ``relative`` uses POSIX separators (the bundle contract).
    """
    if "\\" in relative:
        raise UnsafePathError(f"Backslash separators are not allowed: {relative!r}")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or (pure.parts and pure.parts[0].endswith(":")):
        raise UnsafePathError(f"Absolute paths are not allowed: {relative!r}")
    if not pure.parts:
        raise UnsafePathError("Empty path")
    if ".." in pure.parts:
        raise UnsafePathError(f"Parent traversal ('..') is not allowed: {relative!r}")
    if is_protected_relative(relative):
        raise ProtectedPathError(f"Paths under 'private/' are not allowed: {relative!r}")
    target = (root / Path(*pure.parts)).resolve()
    resolved_root = root.resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise UnsafePathError(f"Path escapes its root: {relative!r}")
    return target
