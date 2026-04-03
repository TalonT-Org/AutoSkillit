"""Tests for execution/pr_analysis.py."""

from __future__ import annotations

from autoskillit.execution.pr_analysis import (
    DOMAIN_PATHS,
    extract_linked_issues,
    is_valid_fidelity_finding,
    partition_files_by_domain,
)

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
