from __future__ import annotations

from pathlib import Path

import pytest

SKILL_MD = (
    Path(__file__).parents[2] / "src/autoskillit/skills_extended/open-integration-pr/SKILL.md"
)


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_MD.read_text()


def test_skill_references_partition_files_by_domain(skill_text: str) -> None:
    assert "partition_files_by_domain" in skill_text


def test_skill_defines_step_4c_domain_partition(skill_text: str) -> None:
    assert "Step 4c" in skill_text


def test_skill_defines_step_4d_domain_diffs(skill_text: str) -> None:
    assert "Step 4d" in skill_text


def test_skill_defines_step_4e_prs_per_domain(skill_text: str) -> None:
    assert "Step 4e" in skill_text


def test_skill_defines_step_4f_domain_commits(skill_text: str) -> None:
    assert "Step 4f" in skill_text


def test_skill_defines_step_4g_parallel_subagents(skill_text: str) -> None:
    assert "Step 4g" in skill_text


def test_skill_spawns_parallel_subagents_for_domains(skill_text: str) -> None:
    assert "Task tool" in skill_text
    assert "model: sonnet" in skill_text


def test_skill_has_at_least_seven_domain_names(skill_text: str) -> None:
    domains = [
        "Server/MCP Tools",
        "Pipeline/Execution",
        "Recipe/Validation",
        "CLI/Workspace",
        "Skills",
        "Tests",
        "Core/Config/Infra",
    ]
    for domain in domains:
        assert domain in skill_text, f"Domain '{domain}' not found in SKILL.md"


def test_skill_body_template_includes_domain_analysis_section(skill_text: str) -> None:
    assert "## Domain Analysis" in skill_text


def test_skill_domain_summaries_before_architecture_impact(skill_text: str) -> None:
    domain_pos = skill_text.find("## Domain Analysis")
    arch_pos = skill_text.find("## Architecture Impact")
    assert domain_pos < arch_pos, "Domain Analysis must appear before Architecture Impact"


def test_skill_subagent_output_json_contract(skill_text: str) -> None:
    assert '"summary"' in skill_text
    assert '"key_changes"' in skill_text
    assert '"pr_numbers"' in skill_text
    assert '"commit_count"' in skill_text


def test_skill_diff_truncation_guard(skill_text: str) -> None:
    assert "12 000" in skill_text or "12000" in skill_text or "12,000" in skill_text


def test_skill_skips_empty_domains(skill_text: str) -> None:
    assert "empty diffs" in skill_text or "removed from" in skill_text
