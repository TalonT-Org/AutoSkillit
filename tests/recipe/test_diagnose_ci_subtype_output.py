"""Contract guard: diagnose-ci SKILL.md must emit failure_subtype output token.

Verifies that diagnose-ci/SKILL.md:
1. Declares failure_subtype in its Output Tokens section
2. Includes a subtype classification decision tree
3. Is consistent with the contract in skill_contracts.yaml
"""

from __future__ import annotations

import re

import pytest

from autoskillit.core import pkg_root
from autoskillit.recipe.contracts import load_bundled_manifest

pytestmark = [pytest.mark.layer("recipe")]

_SKILL_MD = pkg_root() / "skills_extended" / "diagnose-ci" / "SKILL.md"


def _skill_text() -> str:
    assert _SKILL_MD.exists(), f"diagnose-ci SKILL.md not found at {_SKILL_MD}"
    return _SKILL_MD.read_text(encoding="utf-8")


def test_diagnose_ci_skill_md_emits_failure_subtype_token() -> None:
    """SKILL.md Output Tokens section must include 'failure_subtype = {value}'."""
    text = _skill_text()
    assert re.search(r"failure_subtype\s*=\s*\{", text), (
        "diagnose-ci SKILL.md must emit 'failure_subtype = {value}' in the "
        "Output Tokens block so the token is matchable by pipeline on_result: conditions"
    )


def test_diagnose_ci_skill_md_contains_subtype_classification() -> None:
    """SKILL.md must contain a subtype classification section or decision tree."""
    text = _skill_text()
    assert "failure_subtype" in text, (
        "diagnose-ci SKILL.md must reference 'failure_subtype' in its workflow"
    )
    assert any(
        subtype in text
        for subtype in ("flaky", "timing_race", "deterministic", "fixture", "import", "env")
    ), (
        "diagnose-ci SKILL.md must reference at least one of the canonical subtype "
        "values (flaky, timing_race, deterministic, fixture, import, env) in its "
        "classification logic"
    )


def test_diagnose_ci_skill_md_covers_all_subtype_values() -> None:
    """All seven canonical subtype values must appear in the SKILL.md."""
    text = _skill_text()
    required = {"flaky", "timing_race", "deterministic", "fixture", "import", "env", "unknown"}
    missing = {v for v in required if v not in text}
    assert not missing, f"diagnose-ci SKILL.md is missing these failure_subtype values: {missing}"


def test_diagnose_ci_skill_md_consistent_with_contract() -> None:
    """SKILL.md must be consistent with the skill_contracts.yaml declaration."""
    manifest = load_bundled_manifest()
    skill = manifest.get("skills", {}).get("diagnose-ci", {})
    outputs = skill.get("outputs", [])
    declared_names = {o.get("name") for o in outputs}
    text = _skill_text()
    # Both: contract must declare it AND SKILL.md must emit it as a structured token
    assert "failure_subtype" in declared_names, (
        "diagnose-ci must declare failure_subtype in skill_contracts.yaml"
    )
    assert re.search(r"failure_subtype\s*=", text), (
        "diagnose-ci contract declares 'failure_subtype' output but SKILL.md "
        "does not emit it as a structured token"
    )
