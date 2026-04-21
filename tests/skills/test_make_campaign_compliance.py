"""Compliance tests for the make-campaign skill.

Verifies skill classification, placeholder hygiene, and contract registration.
No pytestmark layer marker — tests/skills/ is out of scope for layer markers.
"""

from __future__ import annotations

import re

import yaml

from autoskillit.core import pkg_root
from autoskillit.recipe._skill_placeholder_parser import (
    extract_bash_blocks,
    extract_bash_placeholders,
    extract_declared_ingredients,
    shell_vars_assigned,
)
from tests.skills.test_skill_output_compliance import FIXED_NAME_SKILLS

_CONTRACTS_PATH = pkg_root() / "recipe" / "skill_contracts.yaml"
_SKILL_MD_PATH = pkg_root() / "skills_extended" / "make-campaign" / "SKILL.md"


def test_make_campaign_in_fixed_name_skills() -> None:
    """make-campaign IS in FIXED_NAME_SKILLS (name-based output, no timestamp needed)."""
    assert "make-campaign" in FIXED_NAME_SKILLS, (
        "make-campaign must be in FIXED_NAME_SKILLS — output is identity-based "
        "(.autoskillit/recipes/campaigns/<name>.yaml), no timestamp needed."
    )


def test_make_campaign_no_undefined_placeholders() -> None:
    """No bash code block in make-campaign SKILL.md uses an undefined placeholder."""
    content = _SKILL_MD_PATH.read_text()
    bash_blocks = extract_bash_blocks(content)
    placeholders = extract_bash_placeholders(bash_blocks)
    declared = extract_declared_ingredients(content)
    assigned = shell_vars_assigned(bash_blocks)
    allowed = declared | assigned

    undefined = placeholders - allowed
    assert not undefined, (
        f"make-campaign SKILL.md has undefined placeholders in bash blocks: {undefined!r}. "
        "Either declare them as ingredients or add them to the pseudocode allowlist."
    )


def test_make_campaign_skill_contract_registered() -> None:
    """make-campaign has a skill_contracts.yaml entry with campaign_path file_path output."""
    raw = yaml.safe_load(_CONTRACTS_PATH.read_text())
    skills = raw.get("skills", {}) if isinstance(raw, dict) else {}

    assert "make-campaign" in skills, "make-campaign must be registered in skill_contracts.yaml"
    contract = skills["make-campaign"]
    outputs = contract.get("outputs", [])
    output_names = {o["name"]: o for o in outputs if isinstance(o, dict)}

    assert "campaign_path" in output_names, (
        "make-campaign contract must declare a campaign_path output. "
        f"Found outputs: {list(output_names)!r}"
    )
    assert output_names["campaign_path"].get("type") == "file_path", (
        "campaign_path output must have type='file_path'. "
        f"Got: {output_names['campaign_path'].get('type')!r}"
    )

    patterns = contract.get("expected_output_patterns", [])
    has_path_pattern = any(re.search(r"campaign_path", p) for p in patterns)
    assert has_path_pattern, (
        "make-campaign contract must have an expected_output_pattern referencing campaign_path. "
        f"Found patterns: {patterns!r}"
    )
