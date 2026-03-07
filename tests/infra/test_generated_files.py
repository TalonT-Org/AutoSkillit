"""Tests that generated files with machine-local paths are not tracked in git."""

import re
import subprocess
from pathlib import Path

from autoskillit.core.paths import GENERATED_FILES


def test_generated_files_importable_from_core_paths():
    """GENERATED_FILES is importable from autoskillit.core.paths and is a frozenset[str]."""
    assert isinstance(GENERATED_FILES, frozenset)
    assert len(GENERATED_FILES) > 0
    for entry in GENERATED_FILES:
        assert isinstance(entry, str)


def test_no_generated_files_tracked():
    """Generated config files must not be tracked in git.

    These files contain machine-local absolute paths and are regenerated
    by ``autoskillit install``. Tracking them causes rebase failures in
    worktrees when Claude Code plugin loading rewrites the paths.
    """
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    tracked = set(result.stdout.splitlines())
    tracked_generated = [f for f in GENERATED_FILES if f in tracked]
    assert tracked_generated == [], (
        f"Generated files must not be tracked in git: {tracked_generated}. "
        "Run 'git rm --cached <file>' and add to .gitignore."
    )


def test_gitignore_covers_generated_paths():
    """`.gitignore` must have patterns for all generated config files."""
    gitignore = Path(".gitignore").read_text()
    for path in GENERATED_FILES:
        assert path in gitignore, f"Missing .gitignore entry for generated file: {path}"


def test_generated_files_covers_precommit_pattern():
    """Every entry in GENERATED_FILES must match the no-generated-configs pre-commit pattern."""
    config_text = Path(".pre-commit-config.yaml").read_text()
    # Extract the files: pattern from the no-generated-configs hook
    match = re.search(r"id:\s*no-generated-configs.*?files:\s*'([^']+)'", config_text, re.DOTALL)
    assert match, "Could not find no-generated-configs hook with files: pattern"
    pattern = match.group(1)
    for path in GENERATED_FILES:
        assert re.search(pattern, path), (
            f"GENERATED_FILES entry {path!r} does not match "
            f"no-generated-configs pattern {pattern!r}"
        )
