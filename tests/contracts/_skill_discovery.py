"""Shared SKILL.md discovery helpers for skill-impact-matrix contract tests.

Mirrors the marker regex and dual-root (skills/ + skills_extended/) scan
previously duplicated between test_unaffected_skill_registry.py and
test_skill_impact_matrix_registry.py, so both stay reading from a single
source of truth for the SKILL.md root layout.
"""

from __future__ import annotations

import re
from pathlib import Path

from autoskillit.core import pkg_root

MARKER_RE = re.compile(r'<!--\s*autoskillit:exploration-vector\s+id="')
FOR_EACH_RE = re.compile(r"for_each:\s*exploration_vectors")


def iter_skill_md_files() -> list[Path]:
    """All SKILL.md files under skills/ + skills_extended/, sorted per-root."""
    roots = [pkg_root() / "skills", pkg_root() / "skills_extended"]
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(sorted(root.glob("*/SKILL.md")))
    return files
