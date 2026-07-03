from __future__ import annotations

from pathlib import Path

import pytest

from waloader.paths import (
    ProtectedPathError,
    UnsafePathError,
    ensure_dir,
    ensure_not_protected,
    is_protected_path,
    is_protected_relative,
    safe_join,
)


def test_ensure_dir(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b"
    assert ensure_dir(target) == target
    assert target.is_dir()
    ensure_dir(target)  # idempotent


class TestProtectedPath:
    def test_repo_local_private_is_protected(self, tmp_path: Path) -> None:
        assert is_protected_path(tmp_path / "private", tmp_path)
        assert is_protected_path(tmp_path / "private" / "uv.toml", tmp_path)
        assert is_protected_path(Path("private/nested/deep.txt"), tmp_path)

    def test_non_private_paths_are_fine(self, tmp_path: Path) -> None:
        assert not is_protected_path(tmp_path / "data", tmp_path)
        assert not is_protected_path(tmp_path / "privateer", tmp_path)

    def test_macos_system_private_is_not_protected(self, tmp_path: Path) -> None:
        # /private on macOS backs /tmp, /var, /etc — unrelated to the repo rule.
        assert not is_protected_path(Path("/private/tmp/scratch.txt"), tmp_path)

    def test_ensure_not_protected_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ProtectedPathError):
            ensure_not_protected(tmp_path / "private" / "x", tmp_path)
        ensure_not_protected(tmp_path / "ok.txt", tmp_path)

    def test_relative_guard(self) -> None:
        assert is_protected_relative("private")
        assert is_protected_relative("private/creds.toml")
        assert is_protected_relative("PRIVATE/x")
        assert not is_protected_relative("app/private_utils.py")
        assert not is_protected_relative("privateer/x")


class TestSafeJoin:
    def test_normal_join(self, tmp_path: Path) -> None:
        assert safe_join(tmp_path, "pages/analysis.py") == tmp_path / "pages" / "analysis.py"

    def test_rejects_absolute(self, tmp_path: Path) -> None:
        with pytest.raises(UnsafePathError):
            safe_join(tmp_path, "/etc/passwd")

    def test_rejects_drive_letter(self, tmp_path: Path) -> None:
        with pytest.raises(UnsafePathError):
            safe_join(tmp_path, "C:/windows/system32")

    def test_rejects_backslashes(self, tmp_path: Path) -> None:
        with pytest.raises(UnsafePathError):
            safe_join(tmp_path, "pages\\evil.py")

    def test_rejects_parent_traversal(self, tmp_path: Path) -> None:
        with pytest.raises(UnsafePathError):
            safe_join(tmp_path, "../outside.py")
        with pytest.raises(UnsafePathError):
            safe_join(tmp_path, "ok/../../outside.py")

    def test_rejects_private_prefix(self, tmp_path: Path) -> None:
        with pytest.raises(ProtectedPathError):
            safe_join(tmp_path, "private/uv.toml")

    def test_rejects_empty(self, tmp_path: Path) -> None:
        with pytest.raises(UnsafePathError):
            safe_join(tmp_path, "")
