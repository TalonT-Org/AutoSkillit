from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import SkillSource, load_bundled_agent_definitions
from autoskillit.workspace.skills import _skill_info_from_frontmatter

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]

ROOT = Path(__file__).parents[2]
SKILL_PATH = ROOT / "src" / "autoskillit" / "skills_extended" / "audit-docs" / "SKILL.md"


def test_audit_docs_sidecar_routes_the_ten_existing_evidence_vectors() -> None:
    info = _skill_info_from_frontmatter("audit-docs", SkillSource.BUNDLED_EXTENDED, SKILL_PATH)

    assert not info.invalidities, info.invalidities
    assert len(info.exploration_vectors) == 10
    by_role = {role: [] for role in ("semantic-code-navigator", "repository-impact-profiler")}
    for vector in info.exploration_vectors:
        assert vector.role in by_role
        by_role[vector.role].append(vector.id)
        assert vector.task.task_id == f"audit-docs-{vector.id}"
        assert vector.relationship_classes
    assert by_role["semantic-code-navigator"] == [
        "familiarize-core-config",
        "familiarize-execution-workspace",
        "familiarize-recipe-migration",
        "familiarize-server",
        "familiarize-cli-hooks",
    ]
    assert by_role["repository-impact-profiler"] == [
        "familiarize-skills",
        "crossref-agent-guides",
        "crossref-architecture",
        "crossref-requirements-specs",
        "crossref-recipes-docstrings",
    ]


def test_audit_docs_uses_existing_read_only_luna_explorer_definitions() -> None:
    definitions = {definition.name: definition for definition in load_bundled_agent_definitions()}

    for role in ("semantic-code-navigator", "repository-impact-profiler"):
        projection = definitions[role].codex
        assert projection is not None
        assert projection.model == "gpt-5.6-luna"
        assert projection.reasoning_effort == "max"
        assert projection.sandbox_mode == "read-only"
        assert projection.agents_enabled is False
