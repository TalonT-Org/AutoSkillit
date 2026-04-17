"""Manifest completeness and orphan detection tests for the test-filter manifest.

Completeness test: every non-Python tracked file (outside Bucket A and the ignore
list) must match at least one pattern in .autoskillit/test-filter-manifest.yaml.

Orphan detection test: every manifest pattern must match at least one currently
tracked non-Python file (no dead/stale patterns).
"""

from __future__ import annotations

import functools
import subprocess
from pathlib import Path

import pathspec
import pytest

from autoskillit._test_filter import load_manifest

pytestmark = [pytest.mark.layer("infra")]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST_PATH = _REPO_ROOT / ".autoskillit" / "test-filter-manifest.yaml"

# Files that trigger a full test run (Bucket A) — excluded from manifest coverage check.
# These are already handled before the manifest is consulted.
_BUCKET_A_FILES: frozenset[str] = frozenset(
    {
        "pyproject.toml",
        "uv.lock",
        ".pre-commit-config.yaml",
    }
)

# Files legitimately absent from the manifest (no test directory mapping needed).
# Extend this set when new unmapped file types are added to the repo.
_IGNORE_FILES: frozenset[str] = frozenset(
    {
        "LICENSE",
        "tests/CLAUDE.md",
        # Generated/state files in .autoskillit/ that carry no test-routing signal.
        ".autoskillit/.gitignore",
        ".autoskillit/.onboarded",
        ".autoskillit/sync_manifest.json",
    }
)

# Glob patterns for file types legitimately absent from the manifest.
_IGNORE_PATTERNS: tuple[str, ...] = (
    "*.gif",
    "**/.gitkeep",
)

# Combined PathSpec built once from _IGNORE_PATTERNS to avoid O(files×patterns)
# construction inside _should_be_covered().
_IGNORE_SPEC: pathspec.PathSpec = pathspec.PathSpec.from_lines(
    "gitwildmatch", list(_IGNORE_PATTERNS)
)


@functools.cache
def _tracked_non_python_files() -> tuple[str, ...]:
    """Return all non-Python files currently tracked by git.

    Cached to avoid repeated subprocess calls within a single worker process:
    both _files_to_check() (collection time) and test_manifest_pattern_matches_real_file
    (test execution) call this function.
    """
    result = subprocess.run(
        ["git", "ls-files", "--cached"],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        check=True,
    )
    return tuple(f for f in result.stdout.splitlines() if not f.endswith(".py"))


def _should_be_covered(file_path: str) -> bool:
    """Return True if file_path must be covered by a manifest pattern."""
    if file_path in _BUCKET_A_FILES:
        return False
    if file_path in _IGNORE_FILES:
        return False
    if _IGNORE_SPEC.match_file(file_path):
        return False
    return True


def _files_to_check() -> list[str]:
    """Filtered list of non-Python tracked files that must appear in the manifest."""
    return [f for f in _tracked_non_python_files() if _should_be_covered(f)]


def _manifest_patterns() -> list[str]:
    """All pattern keys from the manifest."""
    return list(load_manifest(_MANIFEST_PATH).keys())


@pytest.mark.parametrize("file_path", _files_to_check())
def test_file_covered_by_manifest(file_path: str) -> None:
    """Every non-Python tracked file (outside Bucket A + ignore list) must match
    at least one manifest pattern.

    Failure means a new non-Python file was added without a manifest entry.
    Fix: add a glob pattern to .autoskillit/test-filter-manifest.yaml, or add
    the file to _IGNORE_FILES / _IGNORE_PATTERNS if no test mapping is needed.
    """
    manifest = load_manifest(_MANIFEST_PATH)
    spec = pathspec.PathSpec.from_lines("gitwildmatch", list(manifest.keys()))
    assert spec.match_file(file_path), (
        f"File {file_path!r} is not covered by any manifest pattern. "
        "Add an entry to .autoskillit/test-filter-manifest.yaml "
        "or add to _IGNORE_FILES / _IGNORE_PATTERNS in this test file."
    )


@pytest.mark.parametrize("pattern", _manifest_patterns())
def test_manifest_pattern_matches_real_file(pattern: str) -> None:
    """Every manifest pattern must match at least one currently tracked non-Python file.

    Failure means the pattern is orphaned — either the files it matched were deleted,
    renamed, or the pattern was misspelled from the start.
    Fix: remove the stale pattern from .autoskillit/test-filter-manifest.yaml or
    correct its path.
    """
    all_tracked = _tracked_non_python_files()
    spec = pathspec.PathSpec.from_lines("gitwildmatch", [pattern])
    matched_files = [f for f in all_tracked if spec.match_file(f)]
    assert matched_files, (
        f"Manifest pattern {pattern!r} matches no tracked files — it may be stale "
        "or misspelled. Remove it from .autoskillit/test-filter-manifest.yaml."
    )
