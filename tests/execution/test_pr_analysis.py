"""Tests for execution/pr_analysis.py."""

from __future__ import annotations

import pytest

from autoskillit.execution.pr_analysis import (
    DOMAIN_PATHS,
    extract_linked_issues,
    is_valid_fidelity_finding,
    partition_files_by_domain,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

# ---------------------------------------------------------------------------
# extract_linked_issues
# ---------------------------------------------------------------------------


def test_extract_linked_issues_closes() -> None:
    assert extract_linked_issues("Closes #123") == ["123"]


def test_extract_linked_issues_fixes() -> None:
    assert extract_linked_issues("Fixes #456") == ["456"]


def test_extract_linked_issues_resolves() -> None:
    assert extract_linked_issues("RESOLVES #789") == ["789"]


def test_extract_linked_issues_deduplication() -> None:
    assert extract_linked_issues("Closes #123\nFixes #123") == ["123"]


def test_extract_linked_issues_sorted() -> None:
    result = extract_linked_issues("Fixes #10\nFixes #5")
    assert result == ["5", "10"]


def test_extract_linked_issues_empty() -> None:
    assert extract_linked_issues("no refs here") == []


def test_extract_linked_issues_multiple() -> None:
    result = extract_linked_issues("Fixes #456\nCloses #123")
    assert result == ["123", "456"]


# ---------------------------------------------------------------------------
# is_valid_fidelity_finding
# ---------------------------------------------------------------------------


def _valid_finding(**overrides: object) -> dict:
    base: dict[str, object] = {
        "dimension": "fidelity",
        "severity": "critical",
        "file": "src/foo.py",
        "line": 42,
        "message": "something wrong",
        "requires_decision": True,
    }
    base.update(overrides)
    return base


def test_is_valid_fidelity_finding_valid_critical() -> None:
    assert is_valid_fidelity_finding(_valid_finding()) is True


def test_is_valid_fidelity_finding_valid_warning() -> None:
    assert is_valid_fidelity_finding(_valid_finding(severity="warning")) is True


def test_is_valid_fidelity_finding_wrong_dimension() -> None:
    assert is_valid_fidelity_finding(_valid_finding(dimension="coverage")) is False


def test_is_valid_fidelity_finding_invalid_severity() -> None:
    assert is_valid_fidelity_finding(_valid_finding(severity="info")) is False


def test_is_valid_fidelity_finding_bool_line() -> None:
    # bool is a subtype of int — must be explicitly rejected
    assert is_valid_fidelity_finding(_valid_finding(line=True)) is False


def test_is_valid_fidelity_finding_missing_field() -> None:
    finding = _valid_finding()
    del finding["message"]  # type: ignore[misc]
    assert is_valid_fidelity_finding(finding) is False


def test_is_valid_fidelity_finding_wrong_type() -> None:
    assert is_valid_fidelity_finding(_valid_finding(file=123)) is False


# ---------------------------------------------------------------------------
# partition_files_by_domain
# ---------------------------------------------------------------------------


def test_partition_files_server_prefix() -> None:
    result = partition_files_by_domain(["src/autoskillit/server/tools_github.py"])
    assert result == {"Server/MCP Tools": ["src/autoskillit/server/tools_github.py"]}


def test_partition_files_tests_prefix() -> None:
    result = partition_files_by_domain(["tests/migration/test_api.py"])
    assert result == {"Tests": ["tests/migration/test_api.py"]}


def test_partition_files_unmatched_goes_to_other() -> None:
    result = partition_files_by_domain(["pyproject.toml"])
    assert result == {"Other": ["pyproject.toml"]}


def test_partition_files_custom_mapping() -> None:
    custom = {"MyDomain": ["custom/"]}
    result = partition_files_by_domain(["custom/foo.py", "other/bar.py"], domain_paths=custom)
    assert result["MyDomain"] == ["custom/foo.py"]
    assert result["Other"] == ["other/bar.py"]


def test_partition_files_first_match_wins() -> None:
    # A file matching two domains via a custom mapping → goes to first
    custom = {"DomainA": ["src/"], "DomainB": ["src/autoskillit/"]}
    result = partition_files_by_domain(["src/autoskillit/foo.py"], domain_paths=custom)
    assert "DomainA" in result
    assert "DomainB" not in result


# ---------------------------------------------------------------------------
# DOMAIN_PATHS constant
# ---------------------------------------------------------------------------


def test_domain_paths_has_expected_domains() -> None:
    expected = {
        "Server/MCP Tools",
        "Pipeline/Execution",
        "Recipe/Validation",
        "CLI/Workspace",
        "Skills",
        "Tests",
        "Core/Config/Infra",
    }
    assert set(DOMAIN_PATHS.keys()) == expected


def test_domain_paths_has_no_duplicates() -> None:
    all_prefixes: list[str] = []
    for prefixes in DOMAIN_PATHS.values():
        all_prefixes.extend(prefixes)
    assert len(all_prefixes) == len(set(all_prefixes))


# ---------------------------------------------------------------------------
# extract_linked_issues — additional coverage (migrated from pipeline/test_fidelity.py)
# ---------------------------------------------------------------------------


def test_extract_linked_issues_case_insensitive() -> None:
    """Extraction is case-insensitive: CLOSES, fixes, Resolves all match."""
    result = extract_linked_issues("CLOSES #10\nfixes #20\nResolves #30")
    assert sorted(result, key=int) == ["10", "20", "30"]


def test_extract_linked_issues_commit_concatenation() -> None:
    """PR body and commit messages concatenated together yield combined results."""
    pr_body = "Closes #100"
    commits = "Fixes #200"
    combined = f"{pr_body}\n{commits}"
    result = extract_linked_issues(combined)
    assert sorted(result, key=int) == ["100", "200"]


# ---------------------------------------------------------------------------
# is_valid_fidelity_finding — edge cases (migrated from pipeline/test_fidelity.py)
# ---------------------------------------------------------------------------


def test_fidelity_gap_with_empty_file_and_zero_line() -> None:
    """Gap finding with file='' and line=0 is valid (unpostable — summary only)."""
    gap = {
        "file": "",
        "line": 0,
        "dimension": "fidelity",
        "severity": "critical",
        "message": "Gap: entire feature section 'notifications' not implemented.",
        "requires_decision": False,
    }
    assert is_valid_fidelity_finding(gap) is True


def test_fidelity_skipped_when_no_linked_issues() -> None:
    """extract_linked_issues returns [] for PRs with no Closes/Fixes/Resolves refs.

    The fidelity skip condition is len(linked_issues) == 0. This test confirms
    that a PR body without issue references produces an empty list, which is the
    signal the review-pr skill uses to skip fidelity subagent launch.
    """
    pr_body = "This PR adds a minor cleanup, no issue references."
    linked = extract_linked_issues(pr_body)
    assert linked == []


# ---------------------------------------------------------------------------
# partition_files_by_domain — domain-key coverage
# (migrated from pipeline/test_pr_domain_partitioner.py)
# ---------------------------------------------------------------------------


def test_partition_files_execution_prefix() -> None:
    result = partition_files_by_domain(["src/autoskillit/execution/headless.py"])
    assert "Pipeline/Execution" in result
    assert "src/autoskillit/execution/headless.py" in result["Pipeline/Execution"]


def test_partition_files_pipeline_prefix() -> None:
    result = partition_files_by_domain(["src/autoskillit/pipeline/pr_gates.py"])
    assert "Pipeline/Execution" in result
    assert "src/autoskillit/pipeline/pr_gates.py" in result["Pipeline/Execution"]


def test_partition_files_recipe_prefix() -> None:
    result = partition_files_by_domain(["src/autoskillit/recipe/schema.py"])
    assert "Recipe/Validation" in result


def test_partition_files_cli_prefix() -> None:
    result = partition_files_by_domain(["src/autoskillit/cli/app.py"])
    assert "CLI/Workspace" in result


def test_partition_files_workspace_prefix() -> None:
    result = partition_files_by_domain(["src/autoskillit/workspace/skills.py"])
    assert "CLI/Workspace" in result


def test_partition_files_skills_prefix() -> None:
    result = partition_files_by_domain(["src/autoskillit/skills_extended/open-pr/SKILL.md"])
    assert "Skills" in result


def test_partition_files_core_prefix() -> None:
    result = partition_files_by_domain(["src/autoskillit/core/types.py"])
    assert "Core/Config/Infra" in result


def test_partition_files_config_prefix() -> None:
    result = partition_files_by_domain(["src/autoskillit/config/settings.py"])
    assert "Core/Config/Infra" in result


def test_partition_files_hooks_prefix() -> None:
    result = partition_files_by_domain(["src/autoskillit/hooks/quota_check.py"])
    assert "Core/Config/Infra" in result


def test_partition_files_empty_input() -> None:
    result = partition_files_by_domain([])
    assert result == {}


def test_partition_files_only_non_empty_domains_returned() -> None:
    result = partition_files_by_domain(["src/autoskillit/server/tools_execution.py"])
    assert "Tests" not in result
    assert "Skills" not in result


def test_partition_files_mixed_domains() -> None:
    files = [
        "src/autoskillit/server/tools_execution.py",
        "src/autoskillit/execution/headless.py",
        "tests/test_something.py",
    ]
    result = partition_files_by_domain(files)
    assert "Server/MCP Tools" in result
    assert "Pipeline/Execution" in result
    assert "Tests" in result
