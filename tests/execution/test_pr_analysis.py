"""Tests for execution/pr_analysis.py (T4 — P2-F1 and P2-F2)."""

from __future__ import annotations


def test_extract_linked_issues_importable_from_execution():
    from autoskillit.execution.pr_analysis import extract_linked_issues

    assert callable(extract_linked_issues)


def test_is_valid_fidelity_finding_importable_from_execution():
    from autoskillit.execution.pr_analysis import is_valid_fidelity_finding

    assert callable(is_valid_fidelity_finding)


def test_domain_paths_importable_from_execution():
    from autoskillit.execution.pr_analysis import DOMAIN_PATHS

    assert isinstance(DOMAIN_PATHS, dict)
    assert "Server/MCP Tools" in DOMAIN_PATHS


def test_partition_files_by_domain_importable_from_execution():
    from autoskillit.execution.pr_analysis import partition_files_by_domain

    assert callable(partition_files_by_domain)


def test_execution_package_exports_pr_analysis_symbols():
    from autoskillit.execution import (  # noqa: F401
        DOMAIN_PATHS,
        extract_linked_issues,
        is_valid_fidelity_finding,
        partition_files_by_domain,
    )
