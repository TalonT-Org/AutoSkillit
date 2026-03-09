"""Tests that generated files with machine-local paths are not tracked in git."""

import re
import subprocess
from pathlib import Path

from autoskillit.core.paths import GENERATED_FILES

REPO_ROOT = Path(__file__).parent.parent.parent


def test_generated_files_importable_from_core_paths():
    """GENERATED_FILES is importable from autoskillit.core.paths and is a frozenset[str]."""
    assert isinstance(GENERATED_FILES, frozenset)
    assert len(GENERATED_FILES) > 0
    for entry in GENERATED_FILES:
        assert isinstance(entry, str)


def test_diagram_directory_in_generated_files():
    """Diagram directory must be in GENERATED_FILES so perform_merge strips it."""
    assert "src/autoskillit/recipes/diagrams/" in GENERATED_FILES, (
        "src/autoskillit/recipes/diagrams/ must be in GENERATED_FILES. "
        "Add it to the frozenset in src/autoskillit/core/paths.py."
    )


def test_no_generated_files_tracked():
    """Generated config and diagram files must not be tracked in git."""
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    tracked = set(result.stdout.splitlines())

    def _tracked_for_entry(entry: str) -> list[str]:
        if entry.endswith("/"):
            return [f for f in tracked if f.startswith(entry)]
        return [entry] if entry in tracked else []

    tracked_generated = [f for entry in GENERATED_FILES for f in _tracked_for_entry(entry)]
    assert tracked_generated == [], (
        f"Generated files must not be tracked in git: {tracked_generated}. "
        "Run 'git rm --cached <file>' and ensure the path is in .gitignore."
    )


def test_gitignore_covers_diagram_directory():
    """Diagram directory must be in .gitignore to prevent accidental commits."""
    gitignore = (REPO_ROOT / ".gitignore").read_text()
    assert "src/autoskillit/recipes/diagrams/" in gitignore, (
        "Missing .gitignore entry for diagram directory. "
        "Add 'src/autoskillit/recipes/diagrams/' to .gitignore."
    )


def test_gitignore_covers_generated_paths():
    """`.gitignore` must have patterns for all generated config files."""
    gitignore = (REPO_ROOT / ".gitignore").read_text()
    for path in GENERATED_FILES:
        assert path in gitignore, f"Missing .gitignore entry for generated file: {path}"


def test_generated_files_covers_precommit_pattern():
    """Every entry in GENERATED_FILES must match the no-generated-configs pre-commit pattern."""
    config_text = (REPO_ROOT / ".pre-commit-config.yaml").read_text()
    # Extract the files: pattern from the no-generated-configs hook
    match = re.search(r"id:\s*no-generated-configs.*?files:\s*'([^']+)'", config_text, re.DOTALL)
    assert match, "Could not find no-generated-configs hook with files: pattern"
    pattern = match.group(1)
    for path in GENERATED_FILES:
        assert re.search(pattern, path), (
            f"GENERATED_FILES entry {path!r} does not match "
            f"no-generated-configs pattern {pattern!r}"
        )
