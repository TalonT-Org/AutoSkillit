"""Tests for the planner L1 subpackage scaffold."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def test_planner_package_importable() -> None:
    import autoskillit.planner  # noqa: F401


def test_planner_all_exports_callables() -> None:
    from autoskillit.planner import __all__

    assert set(__all__) == {
        "build_phase_assignment_manifest",
        "build_phase_wp_manifest",
        "build_pre_elab_snapshot",
        "compile_plan",
        "create_run_dir",
        "expand_assignments",
        "expand_wps",
        "finalize_wp_manifest",
        "validate_plan",
        "PlannerManifest",
        "PlannerManifestItem",
        "merge_files",
        "merge_tier_dir",
        "extract_item",
        "replace_item",
        "build_plan_snapshot",
        "PlanDocument",
        "PhaseShort",
        "PhaseElaborated",
        "AssignmentShort",
        "AssignmentElaborated",
        "WPShort",
        "WPElaborated",
    }


def test_sequential_state_machine_removed() -> None:
    """Guard: sequential state machine functions must not exist in manifests."""
    import autoskillit.planner.manifests as m

    for name in (
        "check_remaining",
        "build_assignment_manifest",
        "build_wp_manifest",
        "_backstop_wp_index",
    ):
        assert not hasattr(m, name), f"{name} still exists — should have been removed"


def test_create_run_dir_creates_timestamped_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_TEMP", str(tmp_path))
    from autoskillit.planner import create_run_dir

    result = create_run_dir()

    assert "planner_dir" in result
    planner_dir = Path(result["planner_dir"])
    assert planner_dir.exists()
    assert planner_dir.parent.name == "planner"
    assert planner_dir.name.startswith("run-")
    assert (planner_dir / "phases").is_dir()
    assert (planner_dir / "assignments").is_dir()
    assert (planner_dir / "work_packages").is_dir()


def test_create_run_dir_unique_across_calls(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTOSKILLIT_TEMP", str(tmp_path))
    from autoskillit.planner import create_run_dir

    r1 = create_run_dir()
    r2 = create_run_dir()

    assert r1["planner_dir"] != r2["planner_dir"]
    planner_dir2 = Path(r2["planner_dir"])
    assert planner_dir2.exists()
    assert planner_dir2.parent.name == "planner"
    assert planner_dir2.name.startswith("run-")
    assert (planner_dir2 / "phases").is_dir()
    assert (planner_dir2 / "assignments").is_dir()
    assert (planner_dir2 / "work_packages").is_dir()


def test_planner_feature_skill_categories() -> None:
    from autoskillit.core._type_constants import FEATURE_REGISTRY

    assert FEATURE_REGISTRY["planner"].skill_categories == frozenset({"planner"})


# --- T6: New callables importable ---


def test_merge_tier_dir_importable() -> None:
    from autoskillit.planner.merge import merge_tier_dir

    assert callable(merge_tier_dir)


def test_expand_assignments_importable() -> None:
    from autoskillit.planner.manifests import expand_assignments

    assert callable(expand_assignments)


def test_expand_wps_importable() -> None:
    from autoskillit.planner.manifests import expand_wps

    assert callable(expand_wps)


# --- T7: New callables functional tests ---


def test_merge_tier_dir_globs_and_merges(tmp_path) -> None:
    from autoskillit.planner.merge import merge_tier_dir

    results_dir = tmp_path / "phases"
    results_dir.mkdir()
    for pid in ("P1", "P2"):
        (results_dir / f"{pid}_result.json").write_text(
            json.dumps({"id": pid, "name": f"Phase {pid}", "ordering": int(pid[1])})
        )
    out = tmp_path / "combined.json"
    result = merge_tier_dir(str(results_dir), str(out), "phases")
    assert result["item_count"] == "2"
    assert out.exists()
    merged = json.loads(out.read_text())
    merged_ids = {item["id"] for item in merged["phases"]}
    assert merged_ids == {"P1", "P2"}


def test_expand_assignments_creates_contexts(tmp_path) -> None:
    from autoskillit.planner.manifests import expand_assignments

    refined = tmp_path / "refined_plan.json"
    refined.write_text(
        json.dumps(
            {
                "phases": [
                    {
                        "id": "P1",
                        "name": "Phase 1",
                        "ordering": 1,
                        "assignments_preview": [{"name": "A1"}, {"name": "A2"}],
                    },
                ]
            }
        )
    )
    result = expand_assignments(str(refined), str(tmp_path))
    assert "manifest_path" in result
    assert "context_paths" in result
    assert "P1" in result.get("item_ids", "")


def test_expand_wps_creates_contexts(tmp_path) -> None:
    from autoskillit.planner.manifests import expand_wps

    refined = tmp_path / "refined_assignments.json"
    refined.write_text(
        json.dumps(
            {
                "assignments": [
                    {
                        "phase_id": "P1",
                        "phase_name": "Phase 1",
                        "phase_number": 1,
                        "assignment_number": 1,
                        "proposed_work_packages": [
                            {"id": "P1-A1-WP1", "name": "WP1", "scope": "scope1"},
                        ],
                    },
                ]
            }
        )
    )
    result = expand_wps(str(refined), str(tmp_path))
    assert "manifest_path" in result
    assert "context_paths" in result
    assert "P1" in result.get("item_ids", "")
