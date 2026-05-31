"""Manifest directory completeness tests for the test-filter manifest.

Validates the pattern-to-directory axis: every manifest pattern must list all
test directories that consume the matched source files.

The SOURCE_DEPENDENCIES registry maps test directory names to the manifest
patterns whose source files those tests consume. A parametrized test cross-
references each declared dependency against the manifest's directory lists,
catching any missing directory at test collection time.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from autoskillit._test_filter import load_manifest

pytestmark = [pytest.mark.layer("infra")]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST_PATH = _REPO_ROOT / ".autoskillit" / "test-filter-manifest.yaml"

# Registry: test directory name -> frozenset of manifest patterns that directory's
# tests consume. Each entry declares a dependency that the manifest must satisfy.
SOURCE_DEPENDENCIES: dict[str, frozenset[str]] = {
    "workspace": frozenset(
        {
            "src/autoskillit/skills/*/SKILL.md",
            "src/autoskillit/skills_extended/*/SKILL.md",
        }
    ),
    "planner": frozenset(
        {
            "src/autoskillit/skills_extended/*/SKILL.md",
            "src/autoskillit/agents/**",
        }
    ),
    "contracts": frozenset(
        {
            "src/autoskillit/assets/**/*",
        }
    ),
    "hooks": frozenset(
        {
            "src/autoskillit/hooks/registry.sha256",
        }
    ),
}


def _dir_is_covered(test_dir: str, manifest_entries: Sequence[str]) -> bool:
    """Check if test_dir is represented in manifest_entries.

    Handles three manifest entry formats:
    - Trailing-slash directory: "planner/" -> strip slash, compare to "planner"
    - Bare directory: "planner" -> compare directly
    - File-level: "workspace/test_skills.py" -> extract first path component
    """
    for entry in manifest_entries:
        normalized = entry.rstrip("/")
        if normalized == test_dir:
            return True
        if "/" in normalized and normalized.split("/")[0] == test_dir:
            return True
    return False


def _dependency_pairs() -> list[tuple[str, str]]:
    pairs = []
    for test_dir, patterns in SOURCE_DEPENDENCIES.items():
        for pattern in sorted(patterns):
            pairs.append((test_dir, pattern))
    return pairs


def _all_dependency_patterns() -> list[str]:
    patterns: set[str] = set()
    for pat_set in SOURCE_DEPENDENCIES.values():
        patterns.update(pat_set)
    return sorted(patterns)


@pytest.mark.parametrize("test_dir,pattern", _dependency_pairs())
def test_manifest_includes_dependent_directory(test_dir: str, pattern: str) -> None:
    """Each declared dependency must appear in the manifest pattern's directory list.

    Failure means a manifest entry routes source file changes to an incomplete set
    of test directories, silently skipping tests that read those source files.
    Fix: add the missing directory (e.g., 'workspace/') to the manifest entry for
    that pattern in .autoskillit/test-filter-manifest.yaml.
    """
    manifest = load_manifest(_MANIFEST_PATH)
    entries = manifest.get(pattern, [])
    if isinstance(entries, str):
        entries = [entries]
    assert _dir_is_covered(test_dir, entries), (
        f"Manifest pattern {pattern!r} is missing test directory {test_dir!r}. "
        f"Current entries: {entries}. "
        f"Add {test_dir + '/'!r} (or a file-level entry like {test_dir + '/test_file.py'!r}) "
        f"to the manifest entry."
    )


@pytest.mark.parametrize("pattern", _all_dependency_patterns())
def test_source_dependency_pattern_exists_in_manifest(pattern: str) -> None:
    """Each pattern referenced in SOURCE_DEPENDENCIES must exist in the manifest.

    Failure means a SOURCE_DEPENDENCIES entry is stale — the pattern was removed
    or renamed in the manifest without updating this registry.
    Fix: update SOURCE_DEPENDENCIES to reference the current manifest pattern, or
    re-add the pattern to .autoskillit/test-filter-manifest.yaml.
    """
    manifest = load_manifest(_MANIFEST_PATH)
    assert pattern in manifest, (
        f"SOURCE_DEPENDENCIES references pattern {pattern!r} but it is not a key in the manifest."
    )
