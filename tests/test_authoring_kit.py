"""Doc-sync for the LLM authoring kit: the facts the kit teaches the LLM must
match the platform's real defaults and contracts (same discipline as the
configuration doc-sync tests)."""

from __future__ import annotations

from pathlib import Path

import pytest

from waloader.config import UploadsConfig

KIT = Path("authoring_kit")
EXPECTED_FILES = {
    "README.md",  # operator notes — the only file NOT fed to the LLM
    "SYSTEM_PROMPT.md",
    "01-building-waloader-apps.md",
    "02-previews.md",
    "03-help-and-faq.md",
}


@pytest.fixture(scope="module")
def kit() -> dict[str, str]:
    # collapse whitespace so assertions are immune to prose line-wrapping
    return {
        p.name: " ".join(p.read_text(encoding="utf-8").split())
        for p in KIT.glob("*.md")
    }


class TestKitShape:
    def test_exactly_the_expected_files(self, kit: dict[str, str]) -> None:
        assert set(kit) == EXPECTED_FILES

    def test_system_prompt_references_companions(self, kit: dict[str, str]) -> None:
        prompt = kit["SYSTEM_PROMPT.md"]
        for name in ("01-building-waloader-apps.md", "02-previews.md",
                     "03-help-and-faq.md"):
            assert name in prompt

    def test_operator_readme_explains_wiring(self, kit: dict[str, str]) -> None:
        readme = kit["README.md"]
        assert "SYSTEM_PROMPT.md" in readme
        assert "DESIGN_LANGUAGE.md" in readme


class TestBundleContractTaught:
    """The kit must teach the exact bundle format the parser enforces."""

    def test_bundle_markers(self, kit: dict[str, str]) -> None:
        contract = kit["01-building-waloader-apps.md"]
        assert "toml waloader-bundle" in contract
        assert "bundle_format = 1" in contract
        assert "## file:" in contract
        assert "entrypoint" in contract

    def test_nested_fence_rule_taught(self, kit: dict[str, str]) -> None:
        assert "FOUR backticks" in kit["01-building-waloader-apps.md"]

    def test_dataset_concepts_declaration_taught(self, kit: dict[str, str]) -> None:
        contract = kit["01-building-waloader-apps.md"]
        assert "dataset_concepts" in contract
        assert "auto-creates them at deployment" in contract

    def test_no_outer_fence_rule_taught(self, kit: dict[str, str]) -> None:
        # the classic chat-output failure seen in field testing
        assert "Never wrap the bundle in an outer code fence" in \
            kit["01-building-waloader-apps.md"]
        assert "never wrapped inside an outer code fence" in kit["SYSTEM_PROMPT.md"]

    def test_login_gate_always_included(self, kit: dict[str, str]) -> None:
        contract = kit["01-building-waloader-apps.md"]
        assert "ALWAYS include the gate" in contract
        assert "require_login" in contract

    def test_path_rules_taught(self, kit: dict[str, str]) -> None:
        contract = kit["01-building-waloader-apps.md"]
        assert "private/" in contract
        assert ".streamlit" in contract


class TestPlatformFactsInSync:
    """Numbers quoted to users must track the real defaults."""

    def test_limits_match_config_defaults(self, kit: dict[str, str]) -> None:
        defaults = UploadsConfig()
        faq = kit["03-help-and-faq.md"]
        contract = kit["01-building-waloader-apps.md"]
        assert f"{defaults.max_markdown_bundle_mb} MB" in faq
        assert f"{defaults.max_markdown_bundle_mb} MB" in contract
        assert f"{defaults.max_dataset_file_mb} MB" in faq
        assert f"{defaults.max_bundle_files} files" in faq
        assert f"{defaults.max_bundle_files} files" in contract
        assert defaults.default_excel_sheet_name in faq

    def test_dataset_formats_listed(self, kit: dict[str, str]) -> None:
        faq = kit["03-help-and-faq.md"]
        for extension in UploadsConfig().allowed_dataset_extensions:
            assert f"`{extension}`" in faq


class TestCorePracticesTaught:
    def test_design_language_referenced_everywhere_it_matters(
        self, kit: dict[str, str]
    ) -> None:
        for name in ("SYSTEM_PROMPT.md", "01-building-waloader-apps.md",
                     "02-previews.md"):
            assert "DESIGN_LANGUAGE.md" in kit[name], name

    def test_tests_are_mandatory(self, kit: dict[str, str]) -> None:
        assert "includes tests" in kit["SYSTEM_PROMPT.md"]
        assert "pytest" in kit["01-building-waloader-apps.md"]

    def test_sdk_and_parity_pattern_taught(self, kit: dict[str, str]) -> None:
        contract = kit["01-building-waloader-apps.md"]
        assert "load_dataset" in contract
        assert "require_login" in contract
        assert "ImportError" in contract  # the preview-parity fallback
        assert "sample_data" in contract

    def test_preview_guide_covers_cadence_and_parity(
        self, kit: dict[str, str]
    ) -> None:
        previews = kit["02-previews.md"]
        assert "every change, or only when you ask" in previews
        assert "streamlit run app.py" in previews

    def test_faq_answers_the_canonical_questions(self, kit: dict[str, str]) -> None:
        faq = kit["03-help-and-faq.md"]
        assert "What features are available?" in faq
        assert "Can my app do X?" in faq
        assert "limits of file uploads" in faq
        assert "clients dashboard" in faq  # example prompt template
