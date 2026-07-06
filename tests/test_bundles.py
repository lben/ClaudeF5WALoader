from __future__ import annotations

import pytest

from waloader.services import bundles

META = (
    "```toml waloader-bundle\n"
    'bundle_format = 1\n'
    'entrypoint = "app.py"\n'
    "```\n"
)


def simple_bundle() -> str:
    return (
        "# My generated app\n\n"
        + META
        + "\nSome prose the LLM wrote, which is ignored.\n\n"
        "## file: app.py\n\n"
        "```python\n"
        "import streamlit as st\n"
        "st.title('hi')\n"
        "```\n\n"
        "## file: pages/detail.py\n"
        "```python\n"
        "x = 1\n"
        "```\n"
    )


class TestHappyPath:
    def test_parse_simple(self) -> None:
        parsed = bundles.parse_bundle(simple_bundle())
        assert parsed.entrypoint == "app.py"
        assert parsed.file_paths() == ["app.py", "pages/detail.py"]
        assert parsed.files[0].content == "import streamlit as st\nst.title('hi')"
        assert parsed.warnings == []

    def test_metadata_fields_and_unknown_key_warning(self) -> None:
        text = (
            "```toml waloader-bundle\n"
            "bundle_format = 1\n"
            'entrypoint = "app.py"\n'
            'app_name = "From Bundle"\n'
            'description = "A tool"\n'
            'mystery = true\n'
            "```\n"
            "## file: app.py\n```python\npass\n```\n"
        )
        parsed = bundles.parse_bundle(text)
        assert parsed.app_name == "From Bundle"
        assert parsed.description == "A tool"
        assert any("mystery" in w for w in parsed.warnings)

    def test_nested_fences_with_longer_outer_fence(self) -> None:
        inner = 'st.markdown("""\n```python\nexample shown to users\n```\n""")'
        text = (
            META
            + "## file: app.py\n"
            + "````python\n"
            + inner
            + "\n````\n"
        )
        parsed = bundles.parse_bundle(text)
        assert parsed.files[0].content == inner
        assert "```python" in parsed.files[0].content

    def test_empty_file_allowed(self) -> None:
        text = META + "## file: app.py\n```\n```\n"
        parsed = bundles.parse_bundle(text)
        assert parsed.files[0].content == ""

    def test_heading_case_and_spacing_flexible(self) -> None:
        text = META + "##  FILE:   app.py\n```python\npass\n```\n"
        assert bundles.parse_bundle(text).file_paths() == ["app.py"]

    def test_allowlisted_hidden_paths(self) -> None:
        text = (
            META
            + "## file: app.py\n```python\npass\n```\n"
            + "## file: .streamlit/config.toml\n```toml\n[theme]\n```\n"
            + "## file: .gitignore\n```\n*.pyc\n```\n"
        )
        parsed = bundles.parse_bundle(text)
        assert ".streamlit/config.toml" in parsed.file_paths()
        assert ".gitignore" in parsed.file_paths()

    def test_bytes_entry_ok(self) -> None:
        parsed = bundles.parse_bundle_bytes(simple_bundle().encode("utf-8"))
        assert parsed.entrypoint == "app.py"


class TestMetadataRejections:
    def test_no_metadata_block(self) -> None:
        with pytest.raises(bundles.BundleError, match="No metadata block"):
            bundles.parse_bundle("# just prose\nno fences at all\n")

    def test_first_fence_wrong_info(self) -> None:
        text = "```python\nprint('hi')\n```\n" + META
        with pytest.raises(bundles.BundleError, match="toml waloader-bundle"):
            bundles.parse_bundle(text)

    def test_file_heading_before_metadata(self) -> None:
        text = "## file: app.py\n```python\npass\n```\n" + META
        with pytest.raises(bundles.BundleError, match="metadata block must come before"):
            bundles.parse_bundle(text)

    def test_invalid_toml(self) -> None:
        text = "```toml waloader-bundle\nnot == toml\n```\n"
        with pytest.raises(bundles.BundleError, match="not valid TOML"):
            bundles.parse_bundle(text)

    def test_wrong_bundle_format(self) -> None:
        text = '```toml waloader-bundle\nbundle_format = 2\nentrypoint = "a"\n```\n'
        with pytest.raises(bundles.BundleError, match="bundle_format"):
            bundles.parse_bundle(text)

    def test_missing_entrypoint_key(self) -> None:
        text = "```toml waloader-bundle\nbundle_format = 1\n```\n"
        with pytest.raises(bundles.BundleError, match="entrypoint"):
            bundles.parse_bundle(text)


class TestStructureRejections:
    def test_zero_files(self) -> None:
        with pytest.raises(bundles.BundleError, match="declares no files"):
            bundles.parse_bundle(META)

    def test_entrypoint_not_among_files(self) -> None:
        text = META + "## file: other.py\n```python\npass\n```\n"
        with pytest.raises(bundles.BundleError, match="Entrypoint 'app.py' is not among"):
            bundles.parse_bundle(text)

    def test_duplicate_path(self) -> None:
        text = (
            META
            + "## file: app.py\n```python\npass\n```\n"
            + "## file: app.py\n```python\npass\n```\n"
        )
        with pytest.raises(bundles.BundleError, match="Duplicate file path"):
            bundles.parse_bundle(text)

    def test_heading_without_fence(self) -> None:
        text = META + "## file: app.py\nno code block here\n## file: b.py\n```\nx\n```\n"
        with pytest.raises(bundles.BundleError, match="has no fenced code block"):
            bundles.parse_bundle(text)

    def test_unterminated_fence(self) -> None:
        text = META + "## file: app.py\n```python\nnever closed\n"
        with pytest.raises(bundles.BundleError, match="Unterminated"):
            bundles.parse_bundle(text)

    def test_max_files(self) -> None:
        sections = "".join(
            f"## file: f{i}.py\n```\nx\n```\n" for i in range(3)
        )
        text = (
            '```toml waloader-bundle\nbundle_format = 1\nentrypoint = "f0.py"\n```\n'
            + sections
        )
        with pytest.raises(bundles.BundleError, match="limit is 2"):
            bundles.parse_bundle(text, max_files=2)


class TestPathRejections:
    @pytest.mark.parametrize(
        ("path", "message"),
        [
            ("/etc/passwd", "absolute"),
            ("C:/win/system", "drive letters"),
            ("a\\b.py", "backslashes"),
            ("../escape.py", "'\\.\\.' is not allowed"),
            ("ok/../../escape.py", "'\\.\\.' is not allowed"),
            ("./weird.py", "'\\.' segments"),
            ("private/uv.toml", "private"),
            ("PRIVATE/x.py", "private"),
            (".git/config", "hidden"),
            (".env", "hidden"),
            ("src/.hidden/x.py", "hidden"),
        ],
    )
    def test_rejected_paths(self, path: str, message: str) -> None:
        text = (
            '```toml waloader-bundle\nbundle_format = 1\nentrypoint = "app.py"\n```\n'
            f"## file: {path}\n```\nx\n```\n"
            "## file: app.py\n```\nx\n```\n"
        )
        with pytest.raises(bundles.BundleError, match=message):
            bundles.parse_bundle(text)


class TestBytesBoundary:
    def test_too_large(self) -> None:
        data = b"x" * (2 * 1024 * 1024)
        with pytest.raises(bundles.BundleError, match="limit is 1 MB"):
            bundles.parse_bundle_bytes(data, max_mb=1)

    def test_not_utf8(self) -> None:
        with pytest.raises(bundles.BundleError, match="not valid UTF-8"):
            bundles.parse_bundle_bytes(b"\xff\xfe\x00bad", max_mb=1)


class TestLlmArtifactTolerance:
    """Real-world cruft from corporate LLM exports (field-tested)."""

    def test_trailing_workspaces_note_stripped(self) -> None:
        text = (
            simple_bundle()
            + "\n<workspaces-note>DO NOT REMOVE|abc-123-uuid</workspaces-note>\n\n"
        )
        parsed = bundles.parse_bundle(text)
        assert parsed.file_paths() == ["app.py", "pages/detail.py"]
        # and it never leaks into a reconstructed file
        assert all("workspaces-note" not in f.content for f in parsed.files)

    def test_note_inside_file_content_is_preserved(self) -> None:
        text = (
            META
            + "## file: app.py\n```python\n"
            + "TAG = '<workspaces-note>DO NOT REMOVE|x</workspaces-note>'\n"
            + "```\n"
        )
        parsed = bundles.parse_bundle(text)
        assert "workspaces-note" in parsed.files[0].content

    def test_outer_markdown_wrapper_unwrapped(self) -> None:
        wrapped = "````markdown\n" + simple_bundle() + "````\n"
        parsed = bundles.parse_bundle(wrapped)
        assert parsed.entrypoint == "app.py"
        assert parsed.file_paths() == ["app.py", "pages/detail.py"]

    def test_bare_fence_wrapper_unwrapped(self) -> None:
        wrapped = "`````\n" + simple_bundle() + "\n`````"
        assert bundles.parse_bundle(wrapped).entrypoint == "app.py"

    def test_wrapper_plus_trailing_note(self) -> None:
        wrapped = (
            "````markdown\n" + simple_bundle()
            + "````\n<workspaces-note>DO NOT REMOVE|u</workspaces-note>\n"
        )
        assert bundles.parse_bundle(wrapped).entrypoint == "app.py"

    def test_clean_bundle_is_untouched(self) -> None:
        # the metadata fence has a real info string -> never mistaken for a wrapper
        parsed = bundles.parse_bundle(simple_bundle())
        assert parsed.files[0].content == "import streamlit as st\nst.title('hi')"


class TestDatasetConceptsMetadata:
    def test_declared_concepts_parsed(self) -> None:
        text = (
            "```toml waloader-bundle\n"
            "bundle_format = 1\n"
            'entrypoint = "app.py"\n'
            'dataset_concepts = ["clients", "transactions"]\n'
            "```\n"
            "## file: app.py\n```python\npass\n```\n"
        )
        parsed = bundles.parse_bundle(text)
        assert parsed.dataset_concepts == ["clients", "transactions"]
        assert parsed.warnings == []  # a known key, not a warning

    def test_missing_key_means_empty(self) -> None:
        assert bundles.parse_bundle(simple_bundle()).dataset_concepts == []

    def test_wrong_type_rejected(self) -> None:
        text = (
            "```toml waloader-bundle\n"
            "bundle_format = 1\n"
            'entrypoint = "app.py"\n'
            'dataset_concepts = "clients"\n'
            "```\n"
            "## file: app.py\n```python\npass\n```\n"
        )
        with pytest.raises(bundles.BundleError, match="list of strings"):
            bundles.parse_bundle(text)
