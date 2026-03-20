"""Deterministic diff annotation and findings filter for review-pr.

Parses unified diff output, annotates each + and context line with its
per-file line number as [LNNN], and provides a findings filter with
cardinality assertion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_FILE_HEADER = re.compile(r"^\+\+\+ b/(.+)$")


@dataclass
class FilterResult:
    """Result of partitioning findings against valid line ranges."""

    filtered: list[dict] = field(default_factory=list)
    unpostable: list[dict] = field(default_factory=list)
    all_unpostable: bool = False


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
            start = int(hunk_match.group(1))
            count_str = hunk_match.group(2)
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
            current_line = int(hunk_match.group(1))
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


def filter_findings(
    findings: list[dict],
    valid_ranges: dict[str, list[tuple[int, int]]],
) -> FilterResult:
    """Partition findings into filtered (in-range) and unpostable (out-of-range).

    When valid_ranges is empty, all findings pass through (no filtering possible).
    Sets all_unpostable=True when total findings > 0 and filtered is empty.
    """
    if not findings:
        return FilterResult()

    if not valid_ranges:
        return FilterResult(filtered=list(findings))

    filtered: list[dict] = []
    unpostable: list[dict] = []

    for finding in findings:
        file_path = finding.get("file", "")
        line_num = finding.get("line", 0)
        file_ranges = valid_ranges.get(file_path, [])

        if any(start <= line_num <= end for start, end in file_ranges):
            filtered.append(finding)
        else:
            unpostable.append(finding)

    return FilterResult(
        filtered=filtered,
        unpostable=unpostable,
        all_unpostable=len(findings) > 0 and len(filtered) == 0,
    )
