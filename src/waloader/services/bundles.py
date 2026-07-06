"""Markdown project bundle parser and safety validator.

Contract (goals/G01 §4.4): one UTF-8 markdown file containing
  1. a metadata block — the FIRST fenced code block, info string
     ``toml waloader-bundle`` with TOML body (bundle_format, entrypoint, ...);
  2. file blocks — ``## file: relative/posix/path`` headings, each followed by
     a fenced code block whose content is the file, verbatim.

Fences follow CommonMark: an opening fence of N>=3 backticks (plus optional
info string) closes at the first line whose leading backtick run is >= N and
contains nothing else. File contents containing ``` must therefore be wrapped
in a longer outer fence by the bundle author.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import PurePosixPath

BUNDLE_META_INFO = "toml waloader-bundle"
SUPPORTED_BUNDLE_FORMAT = 1
HIDDEN_ALLOWLIST_DIRS = {".streamlit"}
HIDDEN_ALLOWLIST_FILES = {".gitignore"}

_FILE_HEADING_RE = re.compile(r"^##\s*file:\s*(.+?)\s*$", re.IGNORECASE)
_FENCE_OPEN_RE = re.compile(r"^(`{3,})(.*)$")
# Real-world LLM export artifacts tolerated at the upload boundary:
_WORKSPACES_NOTE_RE = re.compile(r"^<workspaces-note>.*</workspaces-note>\s*$")
_WRAPPER_FENCE_RE = re.compile(r"^(`{3,})\s*(?:markdown|md)?\s*$", re.IGNORECASE)


class BundleError(Exception):
    """User-facing bundle problem. Messages must be clear and copyable."""


@dataclass(frozen=True)
class BundleFile:
    path: str
    content: str


@dataclass
class ParsedBundle:
    entrypoint: str
    app_name: str = ""
    description: str = ""
    files: list[BundleFile] = field(default_factory=list)
    dataset_concepts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def file_paths(self) -> list[str]:
        return [f.path for f in self.files]


def _strip_trailing_note(lines: list[str]) -> list[str]:
    """Drop trailing blank lines and <workspaces-note> lines (corporate LLM
    exports append one after the document; it must never reach the app)."""
    while lines and (
        not lines[-1].strip() or _WORKSPACES_NOTE_RE.match(lines[-1].strip())
    ):
        lines.pop()
    return lines


def _unwrap_outer_fence(lines: list[str]) -> list[str]:
    """Recover bundles pasted inside one big ```markdown fence.

    Chat LLMs often emit the whole bundle inside an outer code block; saved to
    a file, that wrapper makes the first fence 'markdown' instead of the
    metadata block. Unwrap ONLY when the first non-empty line is a bare fence
    with no/markdown info (never a valid metadata opener) and the last
    non-empty line is a long-enough closing fence.
    """
    start = next((i for i, line in enumerate(lines) if line.strip()), None)
    if start is None:
        return lines
    opener = _WRAPPER_FENCE_RE.match(lines[start])
    if opener is None:
        return lines
    end = next(
        (i for i in range(len(lines) - 1, start, -1) if lines[i].strip()), None
    )
    if end is None or end == start:
        return lines
    closer = re.match(rf"^`{{{len(opener.group(1))},}}\s*$", lines[end])
    if closer is None:
        return lines
    return lines[start + 1:end]


def sanitize_bundle_text(text: str) -> str:
    """Upload-boundary tolerance for known LLM export artifacts."""
    lines = _strip_trailing_note(text.splitlines())
    lines = _unwrap_outer_fence(lines)
    lines = _strip_trailing_note(lines)  # a note may sit inside the wrapper too
    return "\n".join(lines)


def validate_bundle_relative_path(path: str) -> None:
    """Reject unsafe bundle file paths with a copyable, specific error."""
    if not path or path.strip() != path:
        raise BundleError(f"Invalid file path {path!r}: empty or surrounded by whitespace")
    if "\\" in path:
        raise BundleError(
            f"Invalid file path {path!r}: use forward slashes ('/'), not backslashes"
        )
    pure = PurePosixPath(path)
    if pure.is_absolute():
        raise BundleError(f"Invalid file path {path!r}: absolute paths are not allowed")
    raw_segments = path.split("/")
    if "" in raw_segments:
        raise BundleError(f"Invalid file path {path!r}: empty path segments ('//')")
    if ".." in raw_segments:
        raise BundleError(f"Invalid file path {path!r}: '..' is not allowed")
    if "." in raw_segments:
        raise BundleError(f"Invalid file path {path!r}: '.' segments are not allowed")
    parts = pure.parts
    if not parts:
        raise BundleError(f"Invalid file path {path!r}: empty path")
    if re.match(r"^[A-Za-z]:", parts[0]):
        raise BundleError(f"Invalid file path {path!r}: drive letters are not allowed")
    if parts[0].lower() == "private":
        raise BundleError(f"Invalid file path {path!r}: 'private/' paths are not allowed")
    for index, part in enumerate(parts):
        if not part.startswith("."):
            continue
        is_last = index == len(parts) - 1
        if is_last and part in HIDDEN_ALLOWLIST_FILES and len(parts) == 1:
            continue
        if part in HIDDEN_ALLOWLIST_DIRS:
            continue
        raise BundleError(
            f"Invalid file path {path!r}: hidden files/directories are not allowed "
            f"(exceptions: {sorted(HIDDEN_ALLOWLIST_DIRS | HIDDEN_ALLOWLIST_FILES)})"
        )


@dataclass(frozen=True)
class _Fence:
    info: str
    content: str
    start_line: int
    end_index: int  # index of the line AFTER the closing fence


def _read_fence(lines: list[str], start: int) -> _Fence | None:
    """If lines[start] opens a fence, consume it (CommonMark close rule)."""
    match = _FENCE_OPEN_RE.match(lines[start])
    if not match:
        return None
    ticks, info = match.group(1), match.group(2).strip()
    if info.startswith("`"):  # ````` inline-code-ish line, not a fence opener
        return None
    close_re = re.compile(rf"^`{{{len(ticks)},}}\s*$")
    body: list[str] = []
    for index in range(start + 1, len(lines)):
        if close_re.match(lines[index]):
            return _Fence(info, "\n".join(body), start, index + 1)
        body.append(lines[index])
    raise BundleError(
        f"Unterminated fenced code block starting at line {start + 1} "
        f"(opened with {ticks})"
    )


def parse_bundle(text: str, *, max_files: int = 200) -> ParsedBundle:
    lines = sanitize_bundle_text(text).splitlines()

    # --- metadata: the first fence in the document ----------------------
    meta_fence: _Fence | None = None
    cursor = 0
    while cursor < len(lines):
        fence = _read_fence(lines, cursor)
        if fence is not None:
            meta_fence = fence
            break
        if _FILE_HEADING_RE.match(lines[cursor]):
            raise BundleError(
                "The metadata block must come before any '## file:' section. "
                f"Found a file heading first at line {cursor + 1}."
            )
        cursor += 1
    if meta_fence is None:
        raise BundleError(
            "No metadata block found. The bundle must start with a fenced code block "
            "whose info string is 'toml waloader-bundle'."
        )
    if " ".join(meta_fence.info.split()).lower() != BUNDLE_META_INFO:
        raise BundleError(
            "The first fenced code block must be the metadata block with info string "
            f"'toml waloader-bundle' (found {meta_fence.info!r} at line "
            f"{meta_fence.start_line + 1})."
        )
    try:
        meta = tomllib.loads(meta_fence.content)
    except tomllib.TOMLDecodeError as exc:
        raise BundleError(f"Metadata block is not valid TOML: {exc}") from exc

    warnings: list[str] = []
    known_keys = {
        "bundle_format", "entrypoint", "app_name", "description", "dataset_concepts",
    }
    for key in sorted(set(meta) - known_keys):
        warnings.append(f"Unknown metadata key ignored: {key!r}")

    bundle_format = meta.get("bundle_format")
    if bundle_format != SUPPORTED_BUNDLE_FORMAT:
        raise BundleError(
            f"Unsupported or missing bundle_format {bundle_format!r}; "
            f"this WALoader accepts bundle_format = {SUPPORTED_BUNDLE_FORMAT}."
        )
    entrypoint = meta.get("entrypoint")
    if not entrypoint or not isinstance(entrypoint, str):
        raise BundleError("Metadata must declare a non-empty string 'entrypoint'.")
    dataset_concepts = meta.get("dataset_concepts", [])
    if not isinstance(dataset_concepts, list) or not all(
        isinstance(name, str) for name in dataset_concepts
    ):
        raise BundleError(
            "Metadata 'dataset_concepts' must be a list of strings, e.g. "
            'dataset_concepts = ["clients", "transactions"].'
        )

    # --- file sections ---------------------------------------------------
    files: list[BundleFile] = []
    seen: dict[str, int] = {}
    cursor = meta_fence.end_index
    while cursor < len(lines):
        heading = _FILE_HEADING_RE.match(lines[cursor])
        if heading is None:
            cursor += 1
            continue
        raw_path = heading.group(1)
        heading_line = cursor + 1
        # find this section's fence, refusing to cross into the next section
        fence: _Fence | None = None
        probe = cursor + 1
        while probe < len(lines):
            if _FILE_HEADING_RE.match(lines[probe]):
                break
            fence = _read_fence(lines, probe)
            if fence is not None:
                break
            probe += 1
        if fence is None:
            raise BundleError(
                f"File section '{raw_path}' (line {heading_line}) has no fenced "
                "code block with its content."
            )
        validate_bundle_relative_path(raw_path)
        if raw_path in seen:
            raise BundleError(
                f"Duplicate file path {raw_path!r} (lines {seen[raw_path]} and "
                f"{heading_line})."
            )
        seen[raw_path] = heading_line
        files.append(BundleFile(path=raw_path, content=fence.content))
        cursor = fence.end_index

    if not files:
        raise BundleError("The bundle declares no files ('## file: <path>' sections).")
    if len(files) > max_files:
        raise BundleError(
            f"The bundle declares {len(files)} files; the limit is {max_files}."
        )
    if entrypoint not in seen:
        raise BundleError(
            f"Entrypoint {entrypoint!r} is not among the bundle's files: "
            f"{sorted(seen)}"
        )

    return ParsedBundle(
        entrypoint=entrypoint,
        app_name=str(meta.get("app_name", "") or ""),
        description=str(meta.get("description", "") or ""),
        files=files,
        dataset_concepts=dataset_concepts,
        warnings=warnings,
    )


def parse_bundle_bytes(
    data: bytes, *, max_mb: int = 10, max_files: int = 200
) -> ParsedBundle:
    """Upload-boundary entry: size and encoding checks, then parse."""
    limit = max_mb * 1024 * 1024
    if len(data) > limit:
        raise BundleError(
            f"Bundle is {len(data) / 1024 / 1024:.1f} MB; the limit is {max_mb} MB."
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleError(f"Bundle is not valid UTF-8: {exc}") from exc
    return parse_bundle(text, max_files=max_files)
