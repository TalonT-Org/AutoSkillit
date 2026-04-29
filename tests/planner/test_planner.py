"""Tests for the planner L1 subpackage scaffold."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def test_planner_package_importable() -> None:
    import autoskillit.planner  # noqa: F401


def test_planner_all_exports() -> None:
    from autoskillit.planner import __all__

    assert set(__all__) == {
        "build_phase_assignment_manifest",
        "build_phase_wp_manifest",
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
        "ASSIGNMENT_REQUIRED_KEYS",
        "PHASE_REQUIRED_KEYS",
        "WP_REQUIRED_KEYS",
        "resolve_wp_id",
        "validate_refined_assignments",
        "validate_refined_plan",
        "ValidationFinding",
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


def test_create_run_dir_requires_temp_dir_argument() -> None:
    """create_run_dir must accept temp_dir as explicit parameter, not read os.environ."""
    from autoskillit.planner import create_run_dir

    sig = inspect.signature(create_run_dir)
    assert "temp_dir" in sig.parameters, (
        "create_run_dir must accept temp_dir as an explicit keyword argument"
    )
    assert sig.parameters["temp_dir"].default is inspect.Parameter.empty, (
        "temp_dir must be a required parameter (no default)"
    )


def test_create_run_dir_does_not_read_environ(tmp_path, monkeypatch) -> None:
    """create_run_dir must not depend on AUTOSKILLIT_TEMP env var."""
    from autoskillit.planner import create_run_dir

    monkeypatch.delenv("AUTOSKILLIT_TEMP", raising=False)
    result = create_run_dir(temp_dir=str(tmp_path))
    assert result["planner_dir"]
    assert Path(result["planner_dir"]).exists()


def test_create_run_dir_creates_timestamped_directory(tmp_path) -> None:
    from autoskillit.planner import create_run_dir

    result = create_run_dir(temp_dir=str(tmp_path))

    assert "planner_dir" in result
    planner_dir = Path(result["planner_dir"])
    assert planner_dir.exists()
    assert planner_dir.parent.name == "planner"
    assert planner_dir.name.startswith("run-")
    assert (planner_dir / "phases").is_dir()
    assert (planner_dir / "assignments").is_dir()
    assert (planner_dir / "work_packages").is_dir()


def test_create_run_dir_unique_across_calls(tmp_path) -> None:
    from autoskillit.planner import create_run_dir

    r1 = create_run_dir(temp_dir=str(tmp_path))
    r2 = create_run_dir(temp_dir=str(tmp_path))

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


# --- Validation gate tests (ARCH-010) ---


def test_validate_refined_assignments_rejects_empty_id() -> None:
    from autoskillit.planner.schema import validate_refined_assignments

    data = {
        "assignments": [
            {
                "id": "P1-A1",
                "phase_id": "P1",
                "phase_number": 1,
                "assignment_number": 1,
                "proposed_work_packages": [{"name": "No ID", "scope": "s"}],
            }
        ]
    }
    with pytest.raises(ValueError, match="neither 'id' nor 'id_suffix'"):
        validate_refined_assignments(data)


def test_validate_refined_assignments_resolves_id_suffix() -> None:
    from autoskillit.planner.schema import validate_refined_assignments

    data = {
        "assignments": [
            {
                "id": "P1-A1",
                "phase_id": "P1",
                "phase_number": 1,
                "assignment_number": 1,
                "proposed_work_packages": [
                    {"id_suffix": "WP1", "name": "First", "scope": "s1"},
                ],
            }
        ]
    }
    result = validate_refined_assignments(data)
    wp = result["assignments"][0]["proposed_work_packages"][0]
    assert wp["id"] == "P1-A1-WP1"


def test_validate_refined_assignments_rejects_unresolvable_assign_id() -> None:
    from autoskillit.planner.schema import validate_refined_assignments

    data = {
        "assignments": [
            {
                "phase_name": "Phase 1",
                "proposed_work_packages": [{"id_suffix": "WP1", "name": "X"}],
            }
        ]
    }
    with pytest.raises(ValueError, match="assignment.*resolvable id"):
        validate_refined_assignments(data)


def test_validate_refined_plan_rejects_empty_phase_id() -> None:
    from autoskillit.planner.schema import validate_refined_plan

    data = {
        "phases": [
            {
                "id": "",
                "name": "Bad Phase",
                "assignments_preview": [],
            }
        ]
    }
    with pytest.raises(ValueError, match="empty.*id"):
        validate_refined_plan(data)


def test_expand_assignments_rejects_empty_preview_ids(tmp_path) -> None:
    from autoskillit.planner.manifests import expand_assignments

    refined = tmp_path / "refined_plan.json"
    refined.write_text(
        json.dumps(
            {
                "phases": [
                    {
                        "id": "P1",
                        "name": "Phase 1",
                        "assignments_preview": [{"name": "A1"}],
                    }
                ]
            }
        )
    )
    result = expand_assignments(str(refined), str(tmp_path))
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    ids = manifest["items"][0]["metadata"]["assignment_ids"]
    assert all(id_val != "" for id_val in ids)


def test_resolve_wp_id_from_id() -> None:
    from autoskillit.planner.schema import resolve_wp_id

    assert resolve_wp_id({"id": "P1-A1-WP1"}, "P1-A1") == "P1-A1-WP1"


def test_resolve_wp_id_from_suffix() -> None:
    from autoskillit.planner.schema import resolve_wp_id

    assert resolve_wp_id({"id_suffix": "WP2"}, "P1-A1") == "P1-A1-WP2"


def test_resolve_wp_id_raises_on_neither() -> None:
    from autoskillit.planner.schema import resolve_wp_id

    with pytest.raises(ValueError, match="neither 'id' nor 'id_suffix'"):
        resolve_wp_id({"name": "No ID"}, "P1-A1")


def test_resolve_wp_id_rejects_empty_string_id() -> None:
    from autoskillit.planner.schema import resolve_wp_id

    assert resolve_wp_id({"id": "", "id_suffix": "WP1"}, "P1-A1") == "P1-A1-WP1"


def test_expand_wps_resolves_id_suffix(tmp_path) -> None:
    from autoskillit.planner.manifests import expand_wps

    refined = tmp_path / "refined_assignments.json"
    refined.write_text(
        json.dumps(
            {
                "assignments": [
                    {
                        "id": "P1-A1",
                        "phase_id": "P1",
                        "phase_number": 1,
                        "assignment_number": 1,
                        "proposed_work_packages": [
                            {"id_suffix": "WP1", "name": "First", "scope": "s1"},
                            {"id_suffix": "WP2", "name": "Second", "scope": "s2"},
                        ],
                    }
                ]
            }
        )
    )
    result = expand_wps(str(refined), str(tmp_path))
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    wp_ids = manifest["items"][0]["metadata"]["wp_ids"]
    assert wp_ids == ["P1-A1-WP1", "P1-A1-WP2"]


def test_expand_wps_synthesizes_assign_id_from_numbers(tmp_path) -> None:
    from autoskillit.planner.manifests import expand_wps

    refined = tmp_path / "refined_assignments.json"
    refined.write_text(
        json.dumps(
            {
                "assignments": [
                    {
                        "phase_number": 2,
                        "assignment_number": 3,
                        "proposed_work_packages": [
                            {"id_suffix": "WP1", "name": "WP", "scope": "s"},
                        ],
                    }
                ]
            }
        )
    )
    result = expand_wps(str(refined), str(tmp_path))
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    wp_ids = manifest["items"][0]["metadata"]["wp_ids"]
    assert wp_ids == ["P2-A3-WP1"]


def test_expand_wps_raises_on_missing_id_and_suffix(tmp_path) -> None:
    from autoskillit.planner.manifests import expand_wps

    refined = tmp_path / "refined_assignments.json"
    refined.write_text(
        json.dumps(
            {
                "assignments": [
                    {
                        "id": "P1-A1",
                        "phase_id": "P1",
                        "phase_number": 1,
                        "assignment_number": 1,
                        "proposed_work_packages": [
                            {"name": "No ID WP", "scope": "s1"},
                        ],
                    }
                ]
            }
        )
    )
    with pytest.raises(ValueError, match="neither 'id' nor 'id_suffix'"):
        expand_wps(str(refined), str(tmp_path))


def test_write_json_helper_in_conftest() -> None:
    from tests.planner.conftest import write_json

    assert callable(write_json)


def test_merge_files_does_not_use_atomic_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """merge_files must write directly under flock, not via atomic_write/os.replace."""
    from unittest.mock import MagicMock

    from autoskillit.planner.merge import merge_files

    spy = MagicMock(side_effect=AssertionError("atomic_write must not be called"))
    monkeypatch.setattr("autoskillit.planner.merge.write_versioned_json", spy)

    results_dir = tmp_path / "phases"
    results_dir.mkdir()
    (results_dir / "P1_result.json").write_text(
        json.dumps({"id": "P1", "name": "Phase 1", "ordering": 1})
    )

    out = tmp_path / "combined.json"
    result = merge_files([str(results_dir / "P1_result.json")], str(out), "phases")
    assert result["item_count"] == "1"
    merged = json.loads(out.read_text())
    assert merged["schema_version"] == 1
    assert len(merged["phases"]) == 1
    spy.assert_not_called()


def test_replace_item_does_not_use_atomic_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """replace_item must write directly under flock, not via atomic_write/os.replace."""
    from unittest.mock import MagicMock

    from autoskillit.planner.merge import replace_item

    spy = MagicMock(side_effect=AssertionError("atomic_write must not be called"))
    monkeypatch.setattr("autoskillit.planner.merge.write_versioned_json", spy)

    src = tmp_path / "combined.json"
    src.write_text(json.dumps({"phases": [{"id": "P1", "name": "Old"}], "schema_version": 1}))
    repl = tmp_path / "replacement.json"
    repl.write_text(json.dumps({"id": "P1", "name": "New"}))

    result = replace_item(str(src), "P1", str(repl))
    assert result["replaced_id"] == "P1"
    data = json.loads(src.read_text())
    assert data["phases"][0]["name"] == "New"
    assert data["schema_version"] == 1
    spy.assert_not_called()
