"""Tests for _check_gitignored_deliverables — reject WP deliverables in gitignored paths.

The bug class: when make-plan prescribes deliverables whose paths resolve to
.autoskillit/temp/ (gitignored), audit-impl can never verify them via git diff,
creating an unresolvable remediation loop.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from autoskillit.planner.validation import _check_gitignored_deliverables, validate_plan
from tests.planner.conftest import (
    make_assignment_result,
    make_minimal_output_dir,
    make_phase_result,
    make_wp_result,
    write_json,
)

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with .autoskillit/.gitignore containing temp/."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
        check=True,
        capture_output=True,
    )
    gitignore_dir = repo / ".autoskillit"
    gitignore_dir.mkdir()
    (gitignore_dir / ".gitignore").write_text("temp/\n")
    return repo


def test_gitignored_deliverable_rejected(git_repo: Path) -> None:
    wp_results = {
        "P1-A1-WP1": make_wp_result(
            "P1-A1-WP1", deliverables=[".autoskillit/temp/verification_report.md"]
        ),
    }
    findings = _check_gitignored_deliverables(wp_results, repo_root=git_repo)
    assert len(findings) == 1
    assert findings[0]["severity"] == "error"
    assert findings[0]["check"] == "gitignored_deliverable"
    assert "gitignored" in findings[0]["message"].lower()


def test_committable_deliverable_accepted(git_repo: Path) -> None:
    wp_results = {
        "P1-A1-WP1": make_wp_result("P1-A1-WP1", deliverables=["src/autoskillit/core/foo.py"]),
    }
    findings = _check_gitignored_deliverables(wp_results, repo_root=git_repo)
    assert findings == []


def test_multiple_deliverables_mixed(git_repo: Path) -> None:
    wp_results = {
        "P1-A1-WP1": make_wp_result(
            "P1-A1-WP1",
            deliverables=[
                "src/autoskillit/core/ok.py",
                ".autoskillit/temp/report.md",
            ],
        ),
    }
    findings = _check_gitignored_deliverables(wp_results, repo_root=git_repo)
    assert len(findings) == 1
    assert ".autoskillit/temp/report.md" in findings[0]["message"]


def test_nested_gitignore_detected(git_repo: Path) -> None:
    nested = git_repo / "subdir"
    nested.mkdir()
    (nested / ".gitignore").write_text("ignored_subdir/\n")
    ignored = nested / "ignored_subdir"
    ignored.mkdir()
    target = ignored / "artifact.md"
    target.write_text("placeholder")
    relative = str(target.relative_to(git_repo))
    wp_results = {
        "P1-A1-WP1": make_wp_result("P1-A1-WP1", deliverables=[relative]),
    }
    findings = _check_gitignored_deliverables(wp_results, repo_root=git_repo)
    assert len(findings) == 1
    assert findings[0]["check"] == "gitignored_deliverable"


def test_relative_temp_path_detected(git_repo: Path) -> None:
    wp_results = {
        "P1-A1-WP1": make_wp_result(
            "P1-A1-WP1", deliverables=[".autoskillit/temp/some_report.md"]
        ),
    }
    findings = _check_gitignored_deliverables(wp_results, repo_root=git_repo)
    assert len(findings) == 1
    assert findings[0]["check"] == "gitignored_deliverable"


def test_validate_plan_includes_gitignored_deliverable_check(git_repo: Path) -> None:
    """Integration test: validate_plan() wires the gitignored check via source_dir."""
    planner_dir = make_minimal_output_dir(git_repo / "planner_artifacts")
    write_json(
        planner_dir / "phases" / "P1_phase_result.json",
        make_phase_result(1),
    )
    write_json(
        planner_dir / "assignments" / "P1-A1_assignment_result.json",
        make_assignment_result(1, 1),
    )
    write_json(
        planner_dir / "work_packages" / "P1-A1-WP1_wp_result.json",
        make_wp_result("P1-A1-WP1", deliverables=[".autoskillit/temp/bad.md"]),
    )
    result = validate_plan(str(planner_dir), source_dir=str(git_repo))
    assert "gitignored_deliverable" in result.get("verdict", "") or result["verdict"] == "fail"
    validation_data = json.loads((planner_dir / "validation.json").read_text())
    check_names = {f["check"] for f in validation_data.get("findings", [])}
    assert "gitignored_deliverable" in check_names
