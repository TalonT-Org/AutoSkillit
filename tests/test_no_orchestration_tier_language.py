"""Guard: legacy orchestration-tier language must not appear in critical source files."""

from __future__ import annotations

import pathlib

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]

_REPO_ROOT = pathlib.Path(__file__).parent.parent

_ORCHESTRATION_FILES = [
    "src/autoskillit/core/types/_type_enums.py",
    "src/autoskillit/pipeline/gate.py",
    "src/autoskillit/server/_guards.py",
    "src/autoskillit/hooks/leaf_orchestration_guard.py",
]

_FORBIDDEN = [
    "Tier 1 session",
    "Tier 2 worker",
    "mid-tier",
    "bottom-tier",
    "Tier discriminator",
    "Tier 2) may",
    "leaf-tier",
    "Tier invariant",
    "orchestrator-tier",
    "fleet-tier",
]


def test_no_legacy_orchestration_tier_language_in_source():
    """Old ad-hoc tier language must be absent from orchestration-critical files."""
    for rel in _ORCHESTRATION_FILES:
        text = (_REPO_ROOT / rel).read_text()
        for phrase in _FORBIDDEN:
            assert phrase not in text, f"Legacy tier phrase {phrase!r} found in {rel}"
