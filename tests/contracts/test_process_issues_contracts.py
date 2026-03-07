"""Contract tests for the process-issues skill SKILL.md."""

from __future__ import annotations

import pytest

from autoskillit.workspace.skills import bundled_skills_dir


@pytest.fixture
def skill_text() -> str:
    return (bundled_skills_dir() / "process-issues" / "SKILL.md").read_text()


def test_process_issues_emits_result_block(skill_text: str) -> None:
    """process-issues must emit ---process-issues-result--- for pipeline capture."""
    assert "---process-issues-result---" in skill_text


def test_process_issues_reads_triage_manifest(skill_text: str) -> None:
    """process-issues must document consuming the triage manifest JSON."""
    assert "triage_manifest" in skill_text or "triage manifest" in skill_text.lower()


def test_process_issues_routes_by_recipe_label(skill_text: str) -> None:
    """process-issues must document routing by recipe:implementation vs recipe:remediation."""
    assert "recipe:implementation" in skill_text
    assert "recipe:remediation" in skill_text


def test_process_issues_uses_load_recipe(skill_text: str) -> None:
    """process-issues must use load_recipe rather than reimplementing recipe steps."""
    assert "load_recipe" in skill_text


def test_process_issues_respects_batch_order(skill_text: str) -> None:
    """process-issues must document sequential batch ordering."""
    lower = skill_text.lower()
    assert "batch" in lower
    assert "order" in lower or "sequential" in lower or "ascending" in lower


def test_process_issues_supports_dry_run(skill_text: str) -> None:
    """process-issues must document --dry-run flag."""
    assert "--dry-run" in skill_text


def test_process_issues_filters_in_progress(skill_text: str) -> None:
    """process-issues must skip issues already carrying in-progress label."""
    assert "in-progress" in skill_text


def test_process_issues_documents_pr_title_prefix(skill_text: str) -> None:
    """process-issues must document [FEATURE]/[FIX] PR title prefix routing."""
    assert "[FEATURE]" in skill_text
    assert "[FIX]" in skill_text


def test_process_issues_writes_to_temp_dir(skill_text: str) -> None:
    """process-issues must document output to temp/process-issues/."""
    assert "temp/process-issues/" in skill_text


def test_process_issues_supports_merge_batch_flag(skill_text: str) -> None:
    """process-issues must document --merge-batch flag for post-batch PR merging."""
    assert "--merge-batch" in skill_text


def test_process_issues_derives_issue_url(skill_text: str) -> None:
    """process-issues must document constructing issue URL from issue number + repo."""
    assert "issue_url" in skill_text
    assert "github.com" in skill_text or "default_repo" in skill_text


def test_open_pr_supports_run_name_title_prefix() -> None:
    """open-pr must derive [FEATURE]/[FIX] PR title prefix from run_name convention."""
    content = (bundled_skills_dir() / "open-pr" / "SKILL.md").read_text()
    assert "[FEATURE]" in content
    assert "[FIX]" in content
    # Must document the run_name-based convention
    assert "run_name" in content
