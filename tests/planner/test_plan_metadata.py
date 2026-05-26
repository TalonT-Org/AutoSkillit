from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest

from autoskillit.planner.compiler import compile_plan
from autoskillit.planner.manifests import create_run_dir
from tests.planner.conftest import (
    make_assignment_result,
    make_phase_result,
    make_wp_result,
    write_json,
    write_task_file,
)

pytestmark = [pytest.mark.layer("planner"), pytest.mark.medium, pytest.mark.feature("planner")]


# Helpers copied from test_compiler.py


def _make_valid_output_dir(
    tmp_path: Path,
    *,
    num_phases: int = 1,
    with_dep_graph: bool = False,
    dependency_chain: bool = False,
) -> Path:
    """Build a valid output_dir with validation.json verdict=pass."""
    phases_dir = tmp_path / "phases"
    assigns_dir = tmp_path / "assignments"
    wps_dir = tmp_path / "work_packages"

    for p in range(1, num_phases + 1):
        write_json(
            phases_dir / f"P{p}_result.json",
            make_phase_result(p, name=f"Phase {p}"),
        )
        write_json(
            assigns_dir / f"P{p}-A1_result.json",
            make_assignment_result(
                p,
                1,
                name=f"Test Assignment P{p}",
                proposed_work_packages=[f"P{p}-A1-WP1"],
            ),
        )
        deps: list[str] = []
        if dependency_chain and p > 1:
            deps = [f"P{p - 1}-A1-WP1"]
        write_json(
            wps_dir / f"P{p}-A1-WP1_result.json",
            make_wp_result(
                f"P{p}-A1-WP1",
                name=f"WP P{p}-A1-WP1",
                summary=f"Summary P{p}",
                goal=f"Goal P{p}",
                deliverables=[f"src/mod_p{p}.py"],
                technical_steps=[f"step for p{p}"],
                acceptance_criteria=[f"criterion for p{p}"],
                depends_on=deps,
            ),
        )

    manifest_items = [{"id": f"P{p}-A1-WP1", "status": "done"} for p in range(1, num_phases + 1)]
    write_json(
        wps_dir / "wp_manifest.json", {"pass_name": "work_packages", "items": manifest_items}
    )

    write_json(
        tmp_path / "validation.json",
        {"verdict": "pass", "findings": [], "warnings": [], "schema_version": 2},
    )

    if with_dep_graph:
        write_json(
            tmp_path / "dep_graph.json",
            {
                "added_backward_deps": {},
                "forward_deps": {"P1-A1-WP1": ["P1-A1-WP2"]},
            },
        )

    return tmp_path


def _make_chain_3_wps(tmp_path: Path) -> Path:
    """3 WPs: WP1 -> WP2 -> WP3."""
    phases_dir = tmp_path / "phases"
    assigns_dir = tmp_path / "assignments"
    wps_dir = tmp_path / "work_packages"

    write_json(
        phases_dir / "P1_result.json",
        make_phase_result(1, name="Foundation"),
    )
    write_json(
        assigns_dir / "P1-A1_result.json",
        make_assignment_result(
            1,
            1,
            name="Test Assignment",
            proposed_work_packages=["P1-A1-WP1", "P1-A1-WP2", "P1-A1-WP3"],
        ),
    )
    for i, deps in [(1, []), (2, ["P1-A1-WP1"]), (3, ["P1-A1-WP2"])]:
        write_json(
            wps_dir / f"P1-A1-WP{i}_result.json",
            make_wp_result(
                f"P1-A1-WP{i}",
                name=f"WP {i}",
                summary=f"Summary {i}",
                goal=f"Goal {i}",
                deliverables=[f"src/mod{i}.py"],
                technical_steps=[f"step {i}"],
                acceptance_criteria=[f"criterion {i}"],
                depends_on=deps,
            ),
        )
    manifest_items = [{"id": f"P1-A1-WP{i}", "status": "done"} for i in range(1, 4)]
    write_json(
        wps_dir / "wp_manifest.json", {"pass_name": "work_packages", "items": manifest_items}
    )
    write_json(
        tmp_path / "validation.json",
        {"verdict": "pass", "findings": [], "warnings": [], "schema_version": 2},
    )
    return tmp_path


# Tests


def test_create_run_dir_returns_valid_plan_id(tmp_path: Path) -> None:
    result = create_run_dir(str(tmp_path))
    plan_id = result["plan_id"]
    parsed = uuid.UUID(plan_id, version=4)
    assert str(parsed) == plan_id


def test_create_run_dir_captures_source_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True, check=True
    )
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"], cwd=repo, capture_output=True, check=True
    )
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    result = create_run_dir(str(tmp_path), source_dir=str(repo))
    assert result["source_commit"] == expected
    assert len(result["source_commit"]) == 40


def test_create_run_dir_empty_source_commit_without_source_dir(tmp_path: Path) -> None:
    result = create_run_dir(str(tmp_path))
    assert result["source_commit"] == ""


def test_plan_json_includes_plan_metadata(tmp_path: Path) -> None:
    _make_valid_output_dir(tmp_path)
    plan_id = str(uuid.uuid4())
    source_commit = "a" * 40
    compile_plan(
        str(tmp_path),
        write_task_file(tmp_path),
        "/src",
        plan_id=plan_id,
        source_commit=source_commit,
    )
    plan_json = json.loads((tmp_path / "plan.json").read_text())
    assert plan_json["plan_id"] == plan_id
    assert plan_json["source_commit"] == source_commit


def test_manifest_json_includes_plan_id(tmp_path: Path) -> None:
    _make_valid_output_dir(tmp_path)
    plan_id = str(uuid.uuid4())
    compile_plan(
        str(tmp_path), write_task_file(tmp_path), "/src", plan_id=plan_id, source_commit="b" * 40
    )
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["plan_id"] == plan_id


def test_issue_md_has_yaml_front_matter(tmp_path: Path) -> None:
    _make_valid_output_dir(tmp_path)
    plan_id = str(uuid.uuid4())
    source_commit = "c" * 40
    compile_plan(
        str(tmp_path),
        write_task_file(tmp_path),
        "/src",
        plan_id=plan_id,
        source_commit=source_commit,
    )
    issue = (tmp_path / "issues" / "P1-A1-WP1_issue.md").read_text()
    assert issue.startswith("---\n")
    assert f"plan_id: {plan_id}" in issue
    assert f"source_commit: {source_commit}" in issue


def test_plan_md_has_yaml_front_matter(tmp_path: Path) -> None:
    _make_valid_output_dir(tmp_path)
    plan_id = str(uuid.uuid4())
    source_commit = "d" * 40
    result = compile_plan(
        str(tmp_path),
        write_task_file(tmp_path),
        "/src",
        plan_id=plan_id,
        source_commit=source_commit,
    )
    plan_md = Path(result["plan_path"]).read_text()
    assert plan_md.startswith("---\n")
    assert f"plan_id: {plan_id}" in plan_md
    assert f"source_commit: {source_commit}" in plan_md


def test_front_matter_precedes_headings(tmp_path: Path) -> None:
    _make_valid_output_dir(tmp_path)
    compile_plan(
        str(tmp_path),
        write_task_file(tmp_path),
        "/src",
        plan_id=str(uuid.uuid4()),
        source_commit="e" * 40,
    )
    issue = (tmp_path / "issues" / "P1-A1-WP1_issue.md").read_text()
    closing_fence = issue.index("---\n", 4)
    first_heading = issue.index("## Goal")
    assert closing_fence < first_heading


def test_no_front_matter_without_plan_id(tmp_path: Path) -> None:
    _make_valid_output_dir(tmp_path)
    compile_plan(str(tmp_path), write_task_file(tmp_path), "/src")
    issue = (tmp_path / "issues" / "P1-A1-WP1_issue.md").read_text()
    assert not issue.startswith("---")
    plan_json = json.loads((tmp_path / "plan.json").read_text())
    assert "plan_id" not in plan_json


def test_consistent_plan_id_across_all_issues(tmp_path: Path) -> None:
    _make_chain_3_wps(tmp_path)
    plan_id = str(uuid.uuid4())
    source_commit = "f" * 40
    compile_plan(
        str(tmp_path),
        write_task_file(tmp_path),
        "/src",
        plan_id=plan_id,
        source_commit=source_commit,
    )
    for i in range(1, 4):
        issue = (tmp_path / "issues" / f"P1-A1-WP{i}_issue.md").read_text()
        assert f"plan_id: {plan_id}" in issue
        assert f"source_commit: {source_commit}" in issue
