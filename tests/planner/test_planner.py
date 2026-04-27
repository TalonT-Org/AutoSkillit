"""Tests for the planner L1 subpackage scaffold."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def test_planner_package_importable() -> None:
    import autoskillit.planner  # noqa: F401


def test_planner_all_exports_callables() -> None:
    from autoskillit.planner import __all__

    assert set(__all__) == {
        "check_remaining",
        "build_assignment_manifest",
        "build_phase_assignment_manifest",
        "build_phase_wp_manifest",
        "build_pre_elab_snapshot",
        "build_wp_manifest",
        "compile_plan",
        "create_run_dir",
        "finalize_wp_manifest",
        "validate_plan",
        "PlannerManifest",
        "PlannerManifestItem",
        "merge_files",
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
