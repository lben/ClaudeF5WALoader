"""Dependency policy validation for child app pyproject.toml declarations."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field

from packaging.requirements import InvalidRequirement, Requirement

from waloader.config import DependenciesPolicyConfig

VCS_PREFIXES = ("git+", "hg+", "svn+", "bzr+")


class PyprojectError(Exception):
    pass


def extract_dependencies(pyproject_text: str) -> list[str]:
    """Return [project].dependencies from a pyproject.toml, or [] if absent."""
    try:
        data = tomllib.loads(pyproject_text)
    except tomllib.TOMLDecodeError as exc:
        raise PyprojectError(f"pyproject.toml is not valid TOML: {exc}") from exc
    deps = data.get("project", {}).get("dependencies", [])
    if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
        raise PyprojectError("[project].dependencies must be a list of strings")
    return deps


def classify_requirement(requirement: str) -> str:
    """'normal' | 'url' | 'vcs' | 'path' | 'invalid'."""
    try:
        req = Requirement(requirement)
    except InvalidRequirement:
        return "invalid"
    if req.url is None:
        return "normal"
    url = req.url.lower()
    if url.startswith(VCS_PREFIXES):
        return "vcs"
    if url.startswith("file:"):
        return "path"
    return "url"


@dataclass(frozen=True)
class PolicyViolation:
    requirement: str
    reason: str


@dataclass
class PolicyResult:
    allowed: list[str] = field(default_factory=list)
    violations: list[PolicyViolation] = field(default_factory=list)
    needs_approval: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations and not self.needs_approval


def validate_dependencies(
    requirements: list[str],
    policy: DependenciesPolicyConfig,
    *,
    base_dependencies: list[str],
    approved: set[str] | None = None,
) -> PolicyResult:
    """Check declared requirements against the platform policy.

    ``base_dependencies`` is the approved base set (allowed even when
    allow_app_dependencies is false). ``approved`` holds per-app admin
    approvals for require_admin_approval mode.
    """
    result = PolicyResult()
    approved = approved or set()
    base_names = {Requirement(b).name.lower() for b in base_dependencies}

    for requirement in requirements:
        kind = classify_requirement(requirement)
        if kind == "invalid":
            result.violations.append(
                PolicyViolation(requirement, "not a valid PEP 508 requirement")
            )
            continue
        if kind == "url" and not policy.allow_direct_url_dependencies:
            result.violations.append(
                PolicyViolation(
                    requirement,
                    "direct URL dependencies are disabled "
                    "(dependencies_policy.allow_direct_url_dependencies = false)",
                )
            )
            continue
        if kind == "vcs" and not policy.allow_vcs_dependencies:
            result.violations.append(
                PolicyViolation(
                    requirement,
                    "VCS dependencies are disabled "
                    "(dependencies_policy.allow_vcs_dependencies = false)",
                )
            )
            continue
        if kind == "path" and not policy.allow_path_dependencies:
            result.violations.append(
                PolicyViolation(
                    requirement,
                    "local path dependencies are disabled "
                    "(dependencies_policy.allow_path_dependencies = false)",
                )
            )
            continue
        name = Requirement(requirement).name.lower()
        if not policy.allow_app_dependencies and name not in base_names:
            result.violations.append(
                PolicyViolation(
                    requirement,
                    "app dependencies are disabled; only the approved base set is "
                    f"allowed: {sorted(base_names)}",
                )
            )
            continue
        if (
            policy.require_admin_approval_for_new_dependencies
            and requirement not in approved
            and name not in base_names
        ):
            result.needs_approval.append(requirement)
            continue
        result.allowed.append(requirement)
    return result


def format_violations(result: PolicyResult) -> str:
    """Copyable error block for the UI."""
    lines = []
    for violation in result.violations:
        lines.append(f"REJECTED  {violation.requirement}\n  reason: {violation.reason}")
    for requirement in result.needs_approval:
        lines.append(
            f"NEEDS ADMIN APPROVAL  {requirement}\n"
            "  reason: dependencies_policy.require_admin_approval_for_new_dependencies = true"
        )
    return "\n".join(lines)
