"""Static contracts for the single-interpreter run-analysis skill."""

from __future__ import annotations

import pytest

from autoskillit.core import SkillExecutionRole, SkillVisibilitySpec, pkg_root
from autoskillit.core.io import load_yaml
from autoskillit.recipe.contracts import load_bundled_manifest
from autoskillit.workspace import DefaultSkillResolver

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def test_analyze_run_loads_as_bounded_diagnostic_session_skill() -> None:
    resolver = DefaultSkillResolver()
    skill = resolver.resolve("analyze-run")

    assert skill is not None
    assert skill.invalidities == ()
    assert skill.categories == frozenset({"diagnostic"})
    assert skill.uses_capabilities == frozenset()
    assert skill.write_scope is not None
    assert skill.write_scope.kind.value == "bounded"
    assert skill.write_scope.paths == ("{{AUTOSKILLIT_TEMP}}/analyze-run/",)
    assert skill.semantic_plan is None

    catalog = resolver.list_effective(
        None,
        SkillExecutionRole.SESSION,
        visibility=SkillVisibilitySpec(),
        cook_session=True,
    )
    assert "analyze-run" in {entry.name for entry in catalog.skills}

    defaults = load_yaml(pkg_root() / "config" / "defaults.yaml")
    assert "analyze-run" in defaults["skills"]["tier3"]


def test_analyze_run_contract_matches_frozen_packet_workflow() -> None:
    contract = load_bundled_manifest()["skills"]["analyze-run"]
    assert contract["inputs"] == [{"name": "change_id", "type": "string", "required": False}]
    assert contract["outputs"] == [{"name": "report_path", "type": "file_path"}]
    assert contract["completion_required"] is True
    assert contract["expected_output_patterns"] == [
        r"report_path[ \t]*=[ \t]*\S+",
        r"%%ORDER_UP::[a-f0-9]+%%",
    ]

    skill_path = pkg_root() / "skills_extended" / "analyze-run" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    constraints = content.split("## Critical Constraints", 1)[1].split("## Workflow", 1)[0]
    assert "report_path = <absolute path>" in constraints
    assert "interpretation_required" in constraints
    assert ".candidate-findings.json" in constraints
    for required in (
        "snapshot_digest",
        "evidence_ids",
        "analysis-prepare",
        "analysis-finish",
        "No observations in the frozen corpus.",
    ):
        assert required in content
    assert "frozen packet" in content
    assert "skip new interpretation" in " ".join(content.split())
