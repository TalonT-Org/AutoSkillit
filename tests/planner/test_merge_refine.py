"""Tests for autoskillit.planner.merge — refine contexts and merge_refined_assignments."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.planner.merge import (
    _write_refine_contexts,
    merge_refined_assignments,
    merge_refined_wps,
    merge_tier_results,
)
from tests.planner.conftest import (
    make_assignment_result,
    write_json,
    write_task_file,
)

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def _make_assignment(aid: str, phase_id: str, files: list[str]) -> dict:
    return {
        "id": aid,
        "phase_id": phase_id,
        "name": f"Assignment {aid}",
        "goal": f"Goal for {aid}",
        "technical_approach": "",
        "proposed_work_packages": [
            {
                "id": f"{aid}-WP1",
                "name": "WP1",
                "summary": "s",
                "goal": "g",
                "technical_steps": [],
                "files_touched": files,
                "apis_defined": [],
                "apis_consumed": [],
                "depends_on": [],
                "deliverables": ["d1"],
                "acceptance_criteria": [],
            }
        ],
    }


def _write_phase_result(dir_: Path, phase_id: str, assignments: list[dict]) -> None:
    write_json(
        dir_ / f"{phase_id}_result.json",
        {"schema_version": 1, "assignments": assignments},
    )


def test_merge_tier_results_writes_refine_contexts_for_assignments(tmp_path):
    results_dir = tmp_path / "assignments"
    results_dir.mkdir()
    write_json(
        results_dir / "P1-A1_result.json",
        {
            "id": "P1-A1",
            "phase_id": "P1",
            "name": "Auth",
            "goal": "Auth goal",
            "technical_approach": "JWT",
            "proposed_work_packages": [],
        },
    )
    write_json(
        results_dir / "P2-A1_result.json",
        {
            "id": "P2-A1",
            "phase_id": "P2",
            "name": "Data",
            "goal": "Data goal",
            "technical_approach": "ORM",
            "proposed_work_packages": [],
        },
    )
    out = tmp_path / "combined_assignments.json"
    result = merge_tier_results(str(results_dir), str(out), "assignments")
    assert "refine_context_paths" in result
    paths = [p for p in result["refine_context_paths"].split(",") if p.strip()]
    assert len(paths) == 2
    p1_ctx = tmp_path / "refine_contexts" / "context_P1.json"
    p2_ctx = tmp_path / "refine_contexts" / "context_P2.json"
    assert p1_ctx.exists()
    assert p2_ctx.exists()
    assert str(p1_ctx) in paths
    assert str(p2_ctx) in paths


def test_refine_context_own_assignments_in_full_detail(tmp_path):
    results_dir = tmp_path / "assignments"
    results_dir.mkdir()
    write_json(
        results_dir / "P1-A1_result.json",
        {
            "id": "P1-A1",
            "phase_id": "P1",
            "name": "Auth",
            "goal": "Auth goal",
            "technical_approach": "JWT",
            "proposed_work_packages": [{"id": "wp1"}],
        },
    )
    write_json(
        results_dir / "P2-A1_result.json",
        {
            "id": "P2-A1",
            "phase_id": "P2",
            "name": "Data",
            "goal": "Data goal",
            "technical_approach": "ORM",
            "proposed_work_packages": [],
        },
    )
    out = tmp_path / "combined_assignments.json"
    merge_tier_results(str(results_dir), str(out), "assignments")
    ctx = json.loads((tmp_path / "refine_contexts" / "context_P1.json").read_text())
    assert len(ctx["assignments"]) == 1
    own = ctx["assignments"][0]
    assert own["id"] == "P1-A1"
    assert own["technical_approach"] == "JWT"
    assert own["proposed_work_packages"] == [{"id": "wp1"}]
    assert all(a["id"] != "P2-A1" for a in ctx["assignments"])


def test_refine_context_peer_summaries_have_filtered_fields(tmp_path):
    results_dir = tmp_path / "assignments"
    results_dir.mkdir()
    write_json(
        results_dir / "P1-A1_result.json",
        {
            "id": "P1-A1",
            "phase_id": "P1",
            "name": "Auth",
            "goal": "Auth goal",
            "technical_approach": "JWT",
            "proposed_work_packages": [],
        },
    )
    write_json(
        results_dir / "P2-A1_result.json",
        {
            "id": "P2-A1",
            "phase_id": "P2",
            "name": "Data",
            "goal": "Data goal",
            "technical_approach": "ORM",
            "proposed_work_packages": [{"id": "wp2"}],
        },
    )
    out = tmp_path / "combined_assignments.json"
    merge_tier_results(str(results_dir), str(out), "assignments")
    ctx = json.loads((tmp_path / "refine_contexts" / "context_P1.json").read_text())
    assert len(ctx["peer_summaries"]) == 1
    peer = ctx["peer_summaries"][0]
    assert peer == {"id": "P2-A1", "name": "Data", "goal": "Data goal"}
    assert "technical_approach" not in peer
    assert "proposed_work_packages" not in peer


def test_refine_context_has_task_file_path_not_inline_task(tmp_path):
    results_dir = tmp_path / "assignments"
    results_dir.mkdir()
    task_path = write_task_file(tmp_path, "Build a system")
    write_json(
        results_dir / "P1-A1_result.json",
        {
            "id": "P1-A1",
            "phase_id": "P1",
            "name": "Auth",
            "goal": "Auth goal",
            "technical_approach": "",
            "proposed_work_packages": [],
        },
    )
    out = tmp_path / "combined_assignments.json"
    merge_tier_results(str(results_dir), str(out), "assignments", task_file_path=task_path)
    ctx = json.loads((tmp_path / "refine_contexts" / "context_P1.json").read_text())
    assert ctx["task_file_path"] == task_path
    assert "task" not in ctx


def test_refine_context_paths_returned_sorted(tmp_path):
    results_dir = tmp_path / "assignments"
    results_dir.mkdir()
    for pid in ("P3", "P1", "P2"):
        write_json(
            results_dir / f"{pid}-A1_result.json",
            {
                "id": f"{pid}-A1",
                "phase_id": pid,
                "name": pid,
                "goal": f"{pid} goal",
                "technical_approach": "",
                "proposed_work_packages": [],
            },
        )
    out = tmp_path / "combined_assignments.json"
    result = merge_tier_results(str(results_dir), str(out), "assignments")
    paths = [p for p in result["refine_context_paths"].split(",") if p.strip()]
    phase_ids = [Path(p).stem.replace("context_", "") for p in paths]
    assert phase_ids == sorted(phase_ids)
    assert set(phase_ids) == {"P1", "P2", "P3"}


def test_merge_tier_results_no_refine_contexts_for_phases_key(tmp_path):
    results_dir = tmp_path / "phases"
    results_dir.mkdir()
    write_json(
        results_dir / "P1_result.json",
        {
            "id": "P1",
            "name": "Phase 1",
            "ordering": 1,
            "goal": "g",
            "scope": [],
        },
    )
    out = tmp_path / "combined_plan.json"
    result = merge_tier_results(str(results_dir), str(out), "phases")
    assert "refine_context_paths" not in result
    assert not (tmp_path / "refine_contexts").exists()


def _make_wp(wp_id: str, phase_id: str, deliverables: list | None = None) -> dict:
    return {
        "id": wp_id,
        "phase_id": phase_id,
        "name": f"WP {wp_id}",
        "summary": "summary",
        "goal": "goal",
        "scope": f"scope of {wp_id}",
        "technical_steps": ["step1", "step2"],
        "files_touched": [f"src/{wp_id}.py"],
        "apis_defined": [f"{wp_id}.api"],
        "apis_consumed": [],
        "depends_on": [],
        "deliverables": deliverables if deliverables is not None else [f"src/{wp_id}.py"],
        "acceptance_criteria": ["criterion1"],
    }


def _write_wp_result(dir_: Path, wp: dict) -> None:
    write_json(dir_ / f"{wp['id']}_result.json", wp)


# --- Step 1a ---
def test_merge_tier_results_produces_wp_refine_contexts(tmp_path):
    results_dir = tmp_path / "work_packages"
    results_dir.mkdir()
    for phase, wp_id in [("P1", "P1-A1-WP1"), ("P2", "P2-A1-WP1"), ("P3", "P3-A1-WP1")]:
        _write_wp_result(results_dir, _make_wp(wp_id, phase))
    out = tmp_path / "combined_wps.json"
    result = merge_tier_results(str(results_dir), str(out), "work_packages")
    assert "wp_refine_context_paths" in result
    paths = [p for p in result["wp_refine_context_paths"].split(",") if p.strip()]
    assert len(paths) == 3
    for phase in ("P1", "P2", "P3"):
        ctx = tmp_path / "wp_refine_contexts" / f"context_{phase}.json"
        assert ctx.exists()
        assert str(ctx) in paths
    # paths must be sorted
    assert paths == sorted(paths)


# --- Step 1b ---
def test_wp_refine_context_own_phase_in_full_detail(tmp_path):
    results_dir = tmp_path / "work_packages"
    results_dir.mkdir()
    _write_wp_result(results_dir, _make_wp("P1-A1-WP1", "P1"))
    _write_wp_result(results_dir, _make_wp("P2-A1-WP1", "P2"))
    out = tmp_path / "combined_wps.json"
    merge_tier_results(str(results_dir), str(out), "work_packages")
    ctx = json.loads((tmp_path / "wp_refine_contexts" / "context_P1.json").read_text())
    assert len(ctx["work_packages"]) == 1
    own = ctx["work_packages"][0]
    assert own["id"] == "P1-A1-WP1"
    assert "technical_steps" in own
    assert "acceptance_criteria" in own
    assert "files_touched" in own


# --- Step 1c ---
def test_wp_refine_context_peer_summaries_are_stubs(tmp_path):
    results_dir = tmp_path / "work_packages"
    results_dir.mkdir()
    _write_wp_result(results_dir, _make_wp("P1-A1-WP1", "P1"))
    _write_wp_result(results_dir, _make_wp("P2-A1-WP1", "P2"))
    out = tmp_path / "combined_wps.json"
    merge_tier_results(str(results_dir), str(out), "work_packages")
    ctx = json.loads((tmp_path / "wp_refine_contexts" / "context_P1.json").read_text())
    assert len(ctx["peer_summaries"]) == 1
    peer = ctx["peer_summaries"][0]
    assert peer["id"] == "P2-A1-WP1"
    allowed_keys = {"id", "name", "scope", "deliverables", "apis_defined", "apis_consumed"}
    assert set(peer.keys()) <= allowed_keys
    assert allowed_keys <= set(peer.keys())
    assert "technical_steps" not in peer
    assert "acceptance_criteria" not in peer
    assert "files_touched" not in peer


# --- Step 1d ---
def test_wp_refine_context_rejects_unsafe_phase_id(tmp_path):
    results_dir = tmp_path / "work_packages"
    results_dir.mkdir()
    _write_wp_result(results_dir, _make_wp("P1-A1-WP1", "../../evil"))
    out = tmp_path / "combined_wps.json"
    with pytest.raises(ValueError, match="disallowed characters"):
        merge_tier_results(str(results_dir), str(out), "work_packages")


# --- Step 1e ---
def test_wp_refine_context_validates_expected_phases(tmp_path):
    results_dir = tmp_path / "work_packages"
    results_dir.mkdir()
    _write_wp_result(results_dir, _make_wp("P1-A1-WP1", "P1"))
    # manifest at planner_dir (= tmp_path), NOT inside results_dir
    manifest = {"items": [{"id": "P1"}, {"id": "P2"}, {"id": "P3"}]}
    write_json(tmp_path / "phase_wp_manifest.json", manifest)
    out = tmp_path / "combined_wps.json"
    with pytest.raises(ValueError, match="have no merged work packages"):
        merge_tier_results(str(results_dir), str(out), "work_packages")


# --- Step 1f ---
def test_wp_refine_context_corrupt_manifest_raises_valueerror(tmp_path):
    results_dir = tmp_path / "work_packages"
    results_dir.mkdir()
    _write_wp_result(results_dir, _make_wp("P1-A1-WP1", "P1"))
    manifest_path = tmp_path / "phase_wp_manifest.json"
    manifest_path.write_text("{invalid json{{")
    out = tmp_path / "combined_wps.json"
    with pytest.raises(ValueError, match="Corrupted phase_wp_manifest.json"):
        merge_tier_results(str(results_dir), str(out), "work_packages")


# --- Step 2a ---
def _write_wp_phase_result(dir_: Path, phase_id: str, wps: list[dict]) -> None:
    write_json(dir_ / f"{phase_id}_result.json", {"work_packages": wps})


def test_merge_refined_wps_basic(tmp_path):
    ctx_dir = tmp_path / "wp_refine_contexts"
    ctx_dir.mkdir()
    _write_wp_phase_result(
        ctx_dir, "P1", [_make_wp("P1-A1-WP1", "P1"), _make_wp("P1-A1-WP2", "P1")]
    )
    _write_wp_phase_result(
        ctx_dir, "P2", [_make_wp("P2-A1-WP1", "P2"), _make_wp("P2-A1-WP2", "P2")]
    )
    result = merge_refined_wps(planner_dir=str(tmp_path))
    assert "refined_wps_path" in result
    out_path = Path(result["refined_wps_path"])
    assert out_path.exists()
    data = json.loads(out_path.read_text())
    assert len(data["work_packages"]) == 4
    assert result["item_count"] == "4"


# --- Step 2b ---
def test_merge_refined_wps_deliverable_conflict_earlier_wins(tmp_path):
    ctx_dir = tmp_path / "wp_refine_contexts"
    ctx_dir.mkdir()
    _write_wp_phase_result(
        ctx_dir,
        "P1",
        [_make_wp("P1-A1-WP1", "P1", deliverables=["src/shared.py", "src/p1.py"])],
    )
    _write_wp_phase_result(
        ctx_dir,
        "P2",
        [_make_wp("P2-A1-WP1", "P2", deliverables=["src/shared.py", "src/p2.py"])],
    )
    result = merge_refined_wps(planner_dir=str(tmp_path))
    data = json.loads(Path(result["refined_wps_path"]).read_text())
    wps = {wp["id"]: wp for wp in data["work_packages"]}
    assert "src/shared.py" in wps["P1-A1-WP1"]["deliverables"]
    assert "src/shared.py" not in wps["P2-A1-WP1"]["deliverables"]
    assert "src/p2.py" in wps["P2-A1-WP1"]["deliverables"]
    assert result["conflict_count"] == "1"


# --- Step 2c ---
def test_merge_refined_wps_empty_dir_raises(tmp_path):
    ctx_dir = tmp_path / "wp_refine_contexts"
    ctx_dir.mkdir()
    # only context files, no *_result.json
    write_json(ctx_dir / "context_P1.json", {"phase_id": "P1", "work_packages": []})
    with pytest.raises(ValueError, match="No.*_result.json"):
        merge_refined_wps(planner_dir=str(tmp_path))


def test_write_refine_contexts_rejects_unsafe_phase_id(tmp_path):
    results_dir = tmp_path / "assignments"
    results_dir.mkdir()
    write_json(
        results_dir / "P1-A1_result.json",
        {"id": "P1-A1", "phase_id": "../../evil", "name": "x", "goal": "g"},
    )
    out = tmp_path / "combined.json"
    with pytest.raises(ValueError, match="disallowed characters"):
        merge_tier_results(str(results_dir), str(out), "assignments")


def test_merge_refined_assignments_basic(tmp_path):
    ctx_dir = tmp_path / "refine_contexts"
    ctx_dir.mkdir()
    _write_phase_result(
        ctx_dir,
        "P1",
        [
            _make_assignment("P1-A1", "P1", ["src/a.py"]),
            _make_assignment("P1-A2", "P1", ["src/b.py"]),
        ],
    )
    _write_phase_result(
        ctx_dir,
        "P2",
        [
            _make_assignment("P2-A1", "P2", ["src/c.py"]),
            _make_assignment("P2-A2", "P2", ["src/d.py"]),
        ],
    )

    result = merge_refined_assignments(planner_dir=str(tmp_path))

    assert "refined_assignments_path" in result
    out_path = Path(result["refined_assignments_path"])
    assert out_path.exists()
    data = json.loads(out_path.read_text())
    assert len(data["assignments"]) == 4
    assert result["item_count"] == "4"


def test_merge_refined_assignments_wp_conflict_earlier_wins(tmp_path):
    ctx_dir = tmp_path / "refine_contexts"
    ctx_dir.mkdir()
    _write_phase_result(
        ctx_dir,
        "P1",
        [
            _make_assignment("P1-A1", "P1", ["src/foo.py", "src/bar.py"]),
        ],
    )
    _write_phase_result(
        ctx_dir,
        "P2",
        [
            _make_assignment("P2-A1", "P2", ["src/foo.py", "src/baz.py"]),
        ],
    )

    result = merge_refined_assignments(planner_dir=str(tmp_path))

    data = json.loads(Path(result["refined_assignments_path"]).read_text())
    assignments = {a["id"]: a for a in data["assignments"]}

    p1_files = assignments["P1-A1"]["proposed_work_packages"][0]["files_touched"]
    p2_files = assignments["P2-A1"]["proposed_work_packages"][0]["files_touched"]

    assert "src/foo.py" in p1_files
    assert "src/foo.py" not in p2_files
    assert "src/baz.py" in p2_files
    assert result["conflict_count"] == "1"


def test_merge_refined_assignments_no_conflict(tmp_path):
    ctx_dir = tmp_path / "refine_contexts"
    ctx_dir.mkdir()
    _write_phase_result(
        ctx_dir,
        "P1",
        [
            _make_assignment("P1-A1", "P1", ["src/a.py"]),
        ],
    )
    _write_phase_result(
        ctx_dir,
        "P2",
        [
            _make_assignment("P2-A1", "P2", ["src/b.py"]),
        ],
    )

    result = merge_refined_assignments(planner_dir=str(tmp_path))

    data = json.loads(Path(result["refined_assignments_path"]).read_text())
    assignments = {a["id"]: a for a in data["assignments"]}

    assert assignments["P1-A1"]["proposed_work_packages"][0]["files_touched"] == ["src/a.py"]
    assert assignments["P2-A1"]["proposed_work_packages"][0]["files_touched"] == ["src/b.py"]
    assert result["conflict_count"] == "0"


def test_merge_refined_assignments_empty_dir_raises(tmp_path):
    ctx_dir = tmp_path / "refine_contexts"
    ctx_dir.mkdir()
    write_json(ctx_dir / "context_P1.json", {"phase_id": "P1", "assignments": []})

    with pytest.raises(ValueError, match="No.*_result.json"):
        merge_refined_assignments(planner_dir=str(tmp_path))


def test_merge_refined_assignments_writes_to_planner_dir(tmp_path):
    ctx_dir = tmp_path / "refine_contexts"
    ctx_dir.mkdir()
    _write_phase_result(ctx_dir, "P1", [_make_assignment("P1-A1", "P1", ["src/x.py"])])
    _write_phase_result(ctx_dir, "P2", [_make_assignment("P2-A1", "P2", ["src/y.py"])])

    result = merge_refined_assignments(planner_dir=str(tmp_path))

    expected = tmp_path / "refined_assignments.json"
    assert Path(result["refined_assignments_path"]) == expected
    assert expected.exists()


def test_write_refine_contexts_detects_missing_expected_phases(tmp_path: Path) -> None:
    assignments = [
        {
            "id": "P1-A1",
            "phase_id": "P1",
            "name": "Auth",
            "goal": "Auth goal",
            "technical_approach": "JWT",
            "proposed_work_packages": [],
        },
    ]
    expected_phase_ids = frozenset({"P1", "P2", "P3"})

    with pytest.raises(ValueError, match=r"P[23].*have no merged assignments"):
        _write_refine_contexts(
            tmp_path,
            assignments,
            task_file_path="",
            expected_phase_ids=expected_phase_ids,
        )


def test_merge_assignments_succeeds_with_sentinels_present(tmp_path):
    results_dir = tmp_path / "assignments"
    results_dir.mkdir()

    write_json(
        results_dir / "P1-A1_result.json",
        make_assignment_result(1, 1),
    )
    write_json(
        results_dir / "P1-A2_result.json",
        make_assignment_result(1, 2),
    )
    sentinel_dir = results_dir / "assign_sentinels"
    sentinel_dir.mkdir()
    write_json(
        sentinel_dir / "P1_result.json",
        {"id": "P1", "status": "complete", "assignment_count": 2, "failed_count": 0},
    )

    out = tmp_path / "combined.json"
    result = merge_tier_results(str(results_dir), str(out), "assignments")

    assert result["merged_path"] == str(out)
    data = json.loads(out.read_text())
    assert len(data["assignments"]) == 2
    ids = {a["id"] for a in data["assignments"]}
    assert ids == {"P1-A1", "P1-A2"}


def test_merge_refined_assignments_voided_writes_lifecycle_registry(tmp_path):
    ctx_dir = tmp_path / "refine_contexts"
    ctx_dir.mkdir()
    voided_assignment = {
        "id": "P1-A2",
        "phase_id": "P1",
        "name": "Voided",
        "goal": "g",
        "technical_approach": "",
        "proposed_work_packages": [],
    }
    _write_phase_result(
        ctx_dir,
        "P1",
        [_make_assignment("P1-A1", "P1", ["src/a.py"]), voided_assignment],
    )

    merge_refined_assignments(planner_dir=str(tmp_path))

    registry_path = tmp_path / "work_packages" / "lifecycle_registry.json"
    assert registry_path.exists()
    registry = json.loads(registry_path.read_text())
    assert "P1-A2" in registry["voided_assignments"]
