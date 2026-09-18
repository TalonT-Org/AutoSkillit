"""Deterministic diff annotation and findings filter for review-pr.

Parses unified diff output, annotates each + and context line with its
per-file line number as [LNNN], and provides a findings filter with
cardinality assertion.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import regex as re

from autoskillit.core import DiffAnchorAuthority

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_OLD_FILE_HEADER = re.compile(r"^--- a/(.+)$")
_FILE_HEADER = re.compile(r"^\+\+\+ b/(.+)$")


@dataclass
class DiffMetrics:
    """Size metrics computed from a unified diff."""

    added_lines: int
    removed_lines: int
    changed_files: int
    file_paths: list[str] = field(default_factory=list)


_STRUCTURAL_SUFFIXES = (
    "/__init__.py",
    "/setup.py",
    "/setup.cfg",
    "/pyproject.toml",
    "/Makefile",
    "/Taskfile.yml",
    "/Dockerfile",
    "/docker-compose.yml",
    "/docker-compose.yaml",
)

_ALL_STANDARD_AGENTS = ("arch", "tests", "defense", "bugs", "cohesion", "slop")
_SMALL_DIFF_CORE_AGENTS = ("tests", "cohesion")


def compute_diff_metrics(diff_text: str) -> DiffMetrics:
    added = 0
    removed = 0
    file_paths: list[str] = []
    in_hunk = False

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
            continue
        if line.startswith("@@"):
            in_hunk = True
            continue

        if not in_hunk:
            file_match = _FILE_HEADER.match(line)
            if file_match:
                file_paths.append(file_match.group(1))
            continue

        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1

    return DiffMetrics(
        added_lines=added,
        removed_lines=removed,
        changed_files=len(file_paths),
        file_paths=file_paths,
    )


def select_review_agents(
    metrics: DiffMetrics,
    *,
    loc_threshold: int = 200,
    file_threshold: int = 5,
) -> list[str]:
    if metrics.added_lines < loc_threshold and metrics.changed_files < file_threshold:
        agents: list[str] = list(_SMALL_DIFF_CORE_AGENTS)
        if any(("/" + fp).endswith(s) for fp in metrics.file_paths for s in _STRUCTURAL_SUFFIXES):
            agents.insert(0, "arch")
        return agents
    return list(_ALL_STANDARD_AGENTS)


def parse_hunk_ranges(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """Extract per-file valid line ranges from unified diff @@ headers.

    Returns {filepath: [(start, end), ...]} where start/end are new-file
    line numbers (inclusive). Skips pure-deletion hunks (+0,0).
    """
    ranges: dict[str, list[tuple[int, int]]] = {}
    current_file: str | None = None

    for line in diff_text.splitlines():
        file_match = _FILE_HEADER.match(line)
        if file_match:
            current_file = file_match.group(1)
            continue

        hunk_match = _HUNK_HEADER.match(line)
        if hunk_match and current_file is not None:
            start = int(hunk_match.group(3))
            count_str = hunk_match.group(4)
            count = int(count_str) if count_str is not None else 1
            if count == 0:
                continue  # pure deletion hunk
            end = start + count - 1
            ranges.setdefault(current_file, []).append((start, end))

    return ranges


def annotate_diff(diff_text: str) -> str:
    """Annotate each + and context line with [LNNN] per-file line number.

    Deleted lines (- prefix) get no marker. Hunk headers pass through
    unchanged. Line numbering resets at each file boundary.
    """
    output_lines: list[str] = []
    current_line = 0
    in_hunk = False

    for line in diff_text.splitlines():
        if _FILE_HEADER.match(line):
            in_hunk = False
            output_lines.append(line)
            continue

        hunk_match = _HUNK_HEADER.match(line)
        if hunk_match:
            current_line = int(hunk_match.group(3))
            in_hunk = True
            output_lines.append(line)
            continue

        if not in_hunk:
            output_lines.append(line)
            continue

        if line.startswith("-"):
            output_lines.append(line)
        elif line.startswith("+") or line.startswith(" "):
            output_lines.append(f"[L{current_line}]{line}")
            current_line += 1
        else:
            output_lines.append(line)

    return "\n".join(output_lines)


def extract_valid_lines(
    diff_text: str,
) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """Extract exact old-file (LEFT) and new-file (RIGHT) line authorities."""
    left: dict[str, list[int]] = {}
    right: dict[str, list[int]] = {}
    old_file: str | None = None
    new_file: str | None = None
    old_line = 0
    new_line = 0
    in_hunk = False

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            old_file = None
            new_file = None
            in_hunk = False
            continue
        old_match = _OLD_FILE_HEADER.match(line)
        if old_match:
            old_file = old_match.group(1)
            continue
        new_match = _FILE_HEADER.match(line)
        if new_match:
            new_file = new_match.group(1)
            continue

        hunk_match = _HUNK_HEADER.match(line)
        if hunk_match:
            old_line = int(hunk_match.group(1))
            new_line = int(hunk_match.group(3))
            in_hunk = True
            continue

        if not in_hunk:
            continue

        if line.startswith("-"):
            if old_file is not None:
                left.setdefault(old_file, []).append(old_line)
            old_line += 1
        elif line.startswith("+"):
            if new_file is not None:
                right.setdefault(new_file, []).append(new_line)
            new_line += 1
        elif line.startswith(" "):
            if old_file is not None:
                left.setdefault(old_file, []).append(old_line)
            if new_file is not None:
                right.setdefault(new_file, []).append(new_line)
            old_line += 1
            new_line += 1

    return (
        {path: sorted(lines) for path, lines in left.items()},
        {path: sorted(lines) for path, lines in right.items()},
    )


def build_anchor_authority(
    diff_text: str,
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    generation_id: str,
) -> DiffAnchorAuthority:
    """Build the immutable anchor authority produced by one diff generation."""
    left_lines, right_lines = extract_valid_lines(diff_text)
    return DiffAnchorAuthority.authoritative(
        repository=repository,
        pr_number=pr_number,
        head_sha=head_sha,
        generation_id=generation_id,
        right_side_lines=right_lines,
        left_side_lines=left_lines,
    )


_LINE_MARKER = re.compile(r"^\[L(\d+)\]")


def normalize_source_line(source_line: str) -> str:
    """Normalize a live source line for a position-independent anchor digest."""
    return source_line.rstrip()


def hash_source_line(source_line: str) -> str:
    """Return the digest used to anchor one live source line."""
    return hashlib.sha256(normalize_source_line(source_line).encode()).hexdigest()


def extract_annotated_source_line(
    annotated_diff: str,
    file_path: str,
    line: int,
) -> str | None:
    """Extract one source line from an annotated diff anchor.

    The annotation marker and exactly one unified-diff prefix are producer
    metadata. The returned value is otherwise the original source line.
    """
    if type(line) is not int:
        return None
    in_file = False
    matches: list[str] = []

    for raw_line in annotated_diff.splitlines():
        header_match = _FILE_HEADER.match(raw_line)
        if header_match:
            if in_file:
                break
            in_file = header_match.group(1) == file_path
            continue
        if not in_file:
            continue

        marker_match = _LINE_MARKER.match(raw_line)
        if marker_match and int(marker_match.group(1)) == line:
            matches.append(raw_line)

    if len(matches) != 1:
        return None
    marker_match = _LINE_MARKER.match(matches[0])
    if marker_match is None:  # pragma: no cover - guarded by the collection above
        return None
    annotated_source_line = matches[0][marker_match.end() :]
    if not annotated_source_line.startswith(("+", " ")):
        return None
    return annotated_source_line[1:]


def validate_anchor(content: str, line: int, anchor_digest: str) -> tuple[str, int | None]:
    """Resolve a stored anchor against live content without guessing a location."""
    if type(line) is not int or not anchor_digest:
        return ("stale", None)
    source_lines = content.splitlines()
    if (
        1 <= line <= len(source_lines)
        and hash_source_line(source_lines[line - 1]) == anchor_digest
    ):
        return ("fresh", line)

    matching_lines = [
        index
        for index, source_line in enumerate(source_lines, start=1)
        if hash_source_line(source_line) == anchor_digest
    ]
    if len(matching_lines) == 1:
        return ("moved", matching_lines[0])
    return ("stale", None)


def extract_code_region(
    annotated_diff: str,
    file_path: str,
    line: int,
    context_lines: int = 50,
) -> str:
    if not annotated_diff:
        return ""

    lines = annotated_diff.splitlines()
    in_file = False
    collected: list[str] = []
    lo = line - context_lines
    hi = line + context_lines

    for raw_line in lines:
        header_match = _FILE_HEADER.match(raw_line)
        if header_match:
            if in_file:
                break
            if header_match.group(1) == file_path:
                in_file = True
            continue

        if not in_file:
            continue

        marker_match = _LINE_MARKER.match(raw_line)
        if marker_match:
            marker_line = int(marker_match.group(1))
            if lo <= marker_line <= hi:
                collected.append(raw_line)

    return "\n".join(collected)
