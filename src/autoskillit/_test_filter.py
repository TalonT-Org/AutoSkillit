"""Test filter manifest: glob-to-test-directory mapping for non-Python files.

Loads ``.autoskillit/test-filter-manifest.yaml`` and resolves changed non-Python
file paths to the minimal set of test directories that must run.
"""

from __future__ import annotations

from pathlib import Path

import pathspec

from autoskillit.core import load_yaml


def load_manifest(path: Path) -> dict[str, list[str]]:
    """Load the test filter manifest YAML.

    Args:
        path: Absolute path to the manifest YAML file.

    Returns:
        Dict mapping glob pattern strings to lists of test directory strings.

    Raises:
        FileNotFoundError: If the manifest file does not exist.
    """
    data = load_yaml(path)
    if not isinstance(data, dict):
        msg = f"Manifest must be a YAML mapping, got {type(data).__name__}"
        raise TypeError(msg)
    return data


def apply_manifest(
    changed_files: list[str],
    manifest: dict[str, list[str]],
) -> set[str] | None:
    """Resolve changed non-Python files to test directories via the manifest.

    For each manifest entry, compiles the glob pattern using pathspec's
    gitwildmatch and checks whether any changed file matches. Collects the
    union of all matched test directories.

    Args:
        changed_files: Repo-relative paths of changed non-Python files.
        manifest: Output of ``load_manifest()``.

    Returns:
        A set of test directory strings (relative to ``tests/``) if at least
        one file matched a manifest pattern. ``None`` if any changed file
        matched no pattern (fail-open: caller should run the full suite).
    """
    if not changed_files:
        return set()

    compiled = {pat: pathspec.PathSpec.from_lines("gitwildmatch", [pat]) for pat in manifest}

    matched_dirs: set[str] = set()
    unmatched: list[str] = []

    for file_path in changed_files:
        file_matched = False
        for pattern, spec in compiled.items():
            if spec.match_file(file_path):
                matched_dirs.update(manifest[pattern])
                file_matched = True
        if not file_matched:
            unmatched.append(file_path)

    if unmatched:
        return None

    return matched_dirs
