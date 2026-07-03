from __future__ import annotations

import pytest

from waloader.config import DependenciesPolicyConfig
from waloader.services import dependency_policy as dp

BASE = ["streamlit", "pandas", "plotly", "duckdb", "pyarrow"]


class TestExtract:
    def test_extracts_project_dependencies(self) -> None:
        text = '[project]\nname = "x"\ndependencies = ["pandas>=2", "requests"]\n'
        assert dp.extract_dependencies(text) == ["pandas>=2", "requests"]

    def test_missing_section_means_empty(self) -> None:
        assert dp.extract_dependencies('[tool.other]\nx = 1\n') == []

    def test_bad_toml(self) -> None:
        with pytest.raises(dp.PyprojectError, match="not valid TOML"):
            dp.extract_dependencies("not == toml")

    def test_bad_dependency_type(self) -> None:
        with pytest.raises(dp.PyprojectError, match="list of strings"):
            dp.extract_dependencies("[project]\ndependencies = [1]\n")


class TestClassify:
    def test_kinds(self) -> None:
        assert dp.classify_requirement("pandas>=2.0") == "normal"
        assert dp.classify_requirement("pkg @ https://x.com/pkg.whl") == "url"
        assert dp.classify_requirement("pkg @ git+https://github.com/a/b") == "vcs"
        assert dp.classify_requirement("pkg @ file:///local/path") == "path"
        assert dp.classify_requirement("!!nonsense!!") == "invalid"


class TestValidate:
    def test_defaults_allow_normal_reject_special(self) -> None:
        policy = DependenciesPolicyConfig()
        result = dp.validate_dependencies(
            [
                "requests>=2",
                "pkg @ https://x.com/p.whl",
                "pkg2 @ git+https://g.com/a/b",
                "pkg3 @ file:///p",
                "!!bad!!",
            ],
            policy,
            base_dependencies=BASE,
        )
        assert result.allowed == ["requests>=2"]
        reasons = " | ".join(v.reason for v in result.violations)
        assert "direct URL" in reasons
        assert "VCS" in reasons
        assert "path" in reasons
        assert "PEP 508" in reasons
        assert not result.ok

    def test_flags_can_open_each_kind(self) -> None:
        policy = DependenciesPolicyConfig(
            allow_direct_url_dependencies=True,
            allow_vcs_dependencies=True,
            allow_path_dependencies=True,
        )
        result = dp.validate_dependencies(
            [
                "pkg @ https://x.com/p.whl",
                "pkg2 @ git+https://g.com/a/b",
                "pkg3 @ file:///p",
            ],
            policy,
            base_dependencies=BASE,
        )
        assert result.ok and len(result.allowed) == 3

    def test_app_dependencies_disabled_allows_only_base_set(self) -> None:
        policy = DependenciesPolicyConfig(allow_app_dependencies=False)
        result = dp.validate_dependencies(
            ["pandas>=2.0", "streamlit", "requests"], policy, base_dependencies=BASE
        )
        assert result.allowed == ["pandas>=2.0", "streamlit"]
        assert [v.requirement for v in result.violations] == ["requests"]
        assert "approved base set" in result.violations[0].reason

    def test_approval_mode(self) -> None:
        policy = DependenciesPolicyConfig(
            require_admin_approval_for_new_dependencies=True
        )
        result = dp.validate_dependencies(
            ["requests>=2", "numpy", "pandas>=2"],
            policy,
            base_dependencies=BASE,
            approved={"requests>=2"},
        )
        assert result.allowed == ["requests>=2", "pandas>=2"]  # approved + base name
        assert result.needs_approval == ["numpy"]
        assert not result.ok

    def test_format_violations_is_copyable(self) -> None:
        policy = DependenciesPolicyConfig(
            require_admin_approval_for_new_dependencies=True
        )
        result = dp.validate_dependencies(
            ["pkg @ git+https://g.com/a/b", "numpy"], policy, base_dependencies=BASE
        )
        block = dp.format_violations(result)
        assert "REJECTED  pkg @ git+https://g.com/a/b" in block
        assert "NEEDS ADMIN APPROVAL  numpy" in block
