"""Tests that generated files with machine-local paths are not tracked in git."""

import subprocess
from pathlib import Path

# Files generated at install time containing absolute paths.
# Must never be committed — they cause merge failures in worktrees.
_GENERATED_FILES = [
    "src/autoskillit/hooks/hooks.json",
    ".claude/settings.json",
]


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
    tracked_generated = [f for f in _GENERATED_FILES if f in tracked]
    assert tracked_generated == [], (
        f"Generated files must not be tracked in git: {tracked_generated}. "
        "Run 'git rm --cached <file>' and add to .gitignore."
    )


def test_gitignore_covers_generated_paths():
    """`.gitignore` must have patterns for all generated config files."""
    gitignore = Path(".gitignore").read_text()
    for path in _GENERATED_FILES:
        assert path in gitignore, f"Missing .gitignore entry for generated file: {path}"
