"""Semantic guard for make-plan's exact backend-neutral requirements."""

from __future__ import annotations

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.workspace.skill_format import read_skill_frontmatter

pytestmark = [pytest.mark.small]


def test_make_plan_declares_exact_worker_capabilities() -> None:
    parsed = read_skill_frontmatter(pkg_root() / "skills_extended" / "make-plan" / "SKILL.md")
    assert parsed.data is not None
    caps = set(parsed.data.get("uses_capabilities", []))
    assert caps == {"write_audit_disposition_bundle"}
    requirements = parsed.data["semantic_requirements"]
    roles = {role["name"] for role in requirements["logical_roles"]}
    assert roles == {
        "plan-foundation-auditor",
        "plan-interface-mapper",
        "plan-registry-tracer",
    }
    assert {spawn["role"] for spawn in requirements["child_spawns"]} == roles
    assert {policy["role"] for policy in requirements["child_model_policies"]} == roles
    assert {policy["model_class"] for policy in requirements["child_model_policies"]} == {"sonnet"}
