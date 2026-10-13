"""Admission and credit flow for manual and kitchen pipeline trackers."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from autoskillit.core import FinalizedRecipeStep, RecipeFlowEdge
from autoskillit.server.tools.tools_pipeline_tracker import record_pipeline_step
from tests.server._helpers import _make_finalized_projection
from tests.server._pipeline_test_helpers import _grant_success_credit, _setup_project

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


def _tracker_path(tmp_path: Path, pipeline_id: str) -> Path:
    return tmp_path / ".autoskillit" / "temp" / "pipeline_tracker" / f"{pipeline_id}.json"


def _install_projection(
    tool_ctx,
    steps: tuple[FinalizedRecipeStep, ...],
    edges: tuple[RecipeFlowEdge, ...] = (),
) -> None:
    steps = tuple(steps)
    edges = tuple(edges) or tuple(
        RecipeFlowEdge(
            source=source.name,
            edge_type="success",
            target=target.name,
            condition=None,
            result_field=None,
        )
        for source, target in zip(steps, steps[1:])
    )
    projection = _make_finalized_projection(steps=steps, edges=edges)
    tool_ctx.active_recipe_projection = projection
    tool_ctx.active_recipe_steps = {step.name: step for step in projection.ordered_steps}


def _install_implementation_projection(tool_ctx, tmp_path: Path) -> None:
    temp_dir = tmp_path / ".autoskillit" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    result = tool_ctx.recipes.load_and_validate(
        "implementation",
        project_dir=tmp_path,
        temp_dir=temp_dir,
        include_finalized_projection=True,
    )
    projection = result["_finalized_projection"]
    tool_ctx.active_recipe_projection = projection
    tool_ctx.active_recipe_steps = {step.name: step for step in projection.ordered_steps}
    assert projection.for_step("plan").tool == "run_skill"
    assert projection.for_step("clone").tool == "bootstrap_clone"
    assert projection.for_step("salvage_plan").tool == "run_python"
    assert projection.for_step("claim_and_resolve") is None
    assert projection.for_step("create_and_publish") is not None


def _install_plan_and_verify(tool_ctx) -> None:
    _install_projection(
        tool_ctx,
        (
            FinalizedRecipeStep(name="plan", tool="run_skill"),
            FinalizedRecipeStep(name="verify", tool="run_skill"),
        ),
    )


def _release_manual_tracker_leases(tool_ctx) -> None:
    from autoskillit.server._tracker_authority import _release_context_tracker

    for key in tuple(tool_ctx.tracker_leases):
        if key.owner_kind == "manual":
            _release_context_tracker(tool_ctx, key)


def _dependency_gate(tool_ctx, pipeline_id: str, step_name: str):
    from autoskillit.server._tracker_authority import (
        _release_context_tracker,
        _select_tracker_authority,
    )
    from autoskillit.server.tools.tools_execution import _check_pipeline_deps

    target, authority, key, _lease = _select_tracker_authority(
        tool_ctx,
        pipeline_id,
        expected=True,
    )
    try:
        return target, _check_pipeline_deps(step_name, authority)
    finally:
        if key is not None:
            _release_context_tracker(tool_ctx, key)


@pytest.fixture
def pipeline_ctx(tool_ctx_kitchen_open, monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    tool_ctx_kitchen_open.project_dir = tmp_path
    _install_plan_and_verify(tool_ctx_kitchen_open)
    try:
        yield tool_ctx_kitchen_open
    finally:
        _release_manual_tracker_leases(tool_ctx_kitchen_open)


@pytest.mark.anyio
async def test_manual_init_rejects_clone_edge_then_accepts_bare_init(
    pipeline_ctx,
    tmp_path: Path,
) -> None:
    _install_implementation_projection(pipeline_ctx, tmp_path)

    result = json.loads(
        await record_pipeline_step(
            pipeline_id="p",
            op="init",
            dependencies={"plan": ["clone"]},
        )
    )

    assert result["success"] is False
    assert result["stage"] == "preflight:pipeline_tracker_dependencies"
    assert result["retriable"] is False
    assert "plan -> clone" in result["error"]
    assert "bootstrap_clone" in result["error"]
    assert "Recovery:" in result["error"]
    assert not _tracker_path(tmp_path, "p").exists()
    assert pipeline_ctx.tracker_leases == {}

    initialized = json.loads(await record_pipeline_step(pipeline_id="p", op="init"))
    assert initialized["success"] is True


@pytest.mark.anyio
async def test_manual_init_rejects_retained_issue_dependency_mapping(
    pipeline_ctx,
    tmp_path: Path,
) -> None:
    _install_implementation_projection(pipeline_ctx, tmp_path)
    dependencies = {
        "plan": ["clone", "claim_and_resolve", "create_and_publish"],
        "salvage_plan": ["plan"],
        "bind_plan_set": ["plan", "salvage_plan"],
        "check_replan_iteration": ["bind_plan_set"],
        "verify": ["bind_plan_set"],
        "create_and_publish": ["claim_and_resolve"],
        "claim_and_resolve": ["clone"],
    }

    result = json.loads(
        await record_pipeline_step(
            pipeline_id="issue-mapping",
            op="init",
            dependencies=dependencies,
        )
    )

    assert result["success"] is False
    assert result["stage"] == "preflight:pipeline_tracker_dependencies"
    assert "plan -> clone" in result["error"]
    assert "salvage_plan -> plan" in result["error"]
    assert not _tracker_path(tmp_path, "issue-mapping").exists()
    assert pipeline_ctx.tracker_leases == {}


@pytest.mark.anyio
async def test_manual_init_bounds_dependency_diagnostics(
    pipeline_ctx,
    tmp_path: Path,
) -> None:
    pipeline_id = "diagnostic-cap"
    _install_projection(
        pipeline_ctx,
        (FinalizedRecipeStep(name="plan", tool="run_skill"),),
    )
    dependencies = {"plan": [f"missing-{index:02d}" for index in range(11)]}

    result = json.loads(
        await record_pipeline_step(
            pipeline_id=pipeline_id,
            op="init",
            dependencies=dependencies,
        )
    )

    assert result["success"] is False
    assert result["stage"] == "preflight:pipeline_tracker_dependencies"
    error = result["error"]
    assert sum(f"plan -> missing-{index:02d}" in error for index in range(11)) == 10
    assert re.search(r"\b1\b(?=[^\n]*(?:additional|remaining|more|omitted))", error, re.I)
    assert not _tracker_path(tmp_path, pipeline_id).exists()
    assert pipeline_ctx.tracker_leases == {}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("steps", "dependencies", "diagnostic"),
    [
        (
            (FinalizedRecipeStep(name="plan", tool="run_skill"),),
            {"plan": ["ghost"]},
            "ghost",
        ),
        (
            (
                FinalizedRecipeStep(name="clone", tool="bootstrap_clone"),
                FinalizedRecipeStep(name="plan", tool="run_skill"),
            ),
            {"clone": ["plan"]},
            "bootstrap_clone",
        ),
        (
            (FinalizedRecipeStep(name="plan", tool="run_skill"),),
            {"plan": ["plan"]},
            "self",
        ),
        (
            (
                FinalizedRecipeStep(name="plan", tool="run_skill"),
                FinalizedRecipeStep(name="verify", tool="run_skill"),
            ),
            {"plan": ["verify"], "verify": ["plan"]},
            "cycle",
        ),
    ],
    ids=["unknown-prerequisite", "non-skill-dependent", "self-edge", "cycle"],
)
async def test_synthetic_invalid_edges_are_rejected_without_a_tracker(
    pipeline_ctx,
    tmp_path: Path,
    steps,
    dependencies,
    diagnostic: str,
) -> None:
    pipeline_id = f"synthetic-{diagnostic}"
    _install_projection(pipeline_ctx, steps)

    result = json.loads(
        await record_pipeline_step(
            pipeline_id=pipeline_id,
            op="init",
            dependencies=dependencies,
        )
    )

    assert result["success"] is False
    assert result["stage"] == "preflight:pipeline_tracker_dependencies"
    assert diagnostic in result["error"].casefold()
    assert not _tracker_path(tmp_path, pipeline_id).exists()
    assert pipeline_ctx.tracker_leases == {}


@pytest.mark.anyio
async def test_manual_init_rejects_empty_active_step_inventory(
    pipeline_ctx, tmp_path: Path
) -> None:
    pipeline_ctx.active_recipe_steps = {}

    result = json.loads(await record_pipeline_step(pipeline_id="empty-inventory", op="init"))

    assert result["success"] is False
    assert "no active recipe steps" in result["error"]
    assert result["stage"] == "preflight:pipeline_tracker"
    assert not _tracker_path(tmp_path, "empty-inventory").exists()
    assert pipeline_ctx.tracker_leases == {}


@pytest.mark.anyio
async def test_manual_init_is_create_only(pipeline_ctx, tmp_path: Path) -> None:
    pipeline_id = "duplicate-init"
    try:
        first = json.loads(await record_pipeline_step(pipeline_id=pipeline_id, op="init"))
        assert first["success"] is True
        second = json.loads(await record_pipeline_step(pipeline_id=pipeline_id, op="init"))

        assert second["success"] is False
        assert "already been initialized" in second["error"]
        assert second.get("stage")
        assert _tracker_path(tmp_path, pipeline_id).exists()
    finally:
        _release_manual_tracker_leases(pipeline_ctx)


@pytest.mark.anyio
async def test_manual_init_derivation_failure_releases_lease_and_writes_no_file(
    pipeline_ctx,
    tmp_path: Path,
) -> None:
    pipeline_id = "broken-projection"
    pipeline_ctx.active_recipe_projection = object()

    result = json.loads(await record_pipeline_step(pipeline_id=pipeline_id, op="init"))

    assert result["success"] is False
    assert result["stage"] == "pipeline_tracker:init"
    assert not _tracker_path(tmp_path, pipeline_id).exists()
    assert pipeline_ctx.tracker_leases == {}


@pytest.mark.anyio
async def test_manual_edge_is_credited_before_dependency_gate_opens(
    pipeline_ctx,
    tmp_path: Path,
) -> None:
    pipeline_id = "manual-credit"
    try:
        initialized = json.loads(
            await record_pipeline_step(
                pipeline_id=pipeline_id,
                op="init",
                dependencies={"verify": ["plan"]},
            )
        )
        assert initialized["success"] is True

        target, denial = _dependency_gate(pipeline_ctx, pipeline_id, "verify")
        assert target is not None
        assert denial is not None
        assert "DEPENDENCY UNMET" in denial

        _grant_success_credit(pipeline_ctx, tmp_path, "plan", pipeline_id=pipeline_id)
        completed = json.loads(
            await record_pipeline_step(
                pipeline_id=pipeline_id,
                op="complete",
                step_name="plan",
            )
        )
        assert completed["success"] is True
        tracker = json.loads(_tracker_path(tmp_path, pipeline_id).read_text())
        assert tracker["steps"]["plan"]["status"] == "complete"

        refreshed_target, admitted = _dependency_gate(pipeline_ctx, pipeline_id, "verify")
        assert refreshed_target is not None
        assert admitted is None
    finally:
        _release_manual_tracker_leases(pipeline_ctx)


@pytest.mark.anyio
async def test_manual_init_unions_derived_and_requested_dependencies(pipeline_ctx) -> None:
    _install_projection(
        pipeline_ctx,
        (
            FinalizedRecipeStep(name="plan", tool="run_skill"),
            FinalizedRecipeStep(name="rectify", tool="run_skill"),
            FinalizedRecipeStep(name="review_approach", tool="run_skill"),
        ),
        edges=(
            RecipeFlowEdge(
                source="plan",
                edge_type="success",
                target="rectify",
                condition=None,
                result_field=None,
            ),
            RecipeFlowEdge(
                source="rectify",
                edge_type="success",
                target="review_approach",
                condition=None,
                result_field=None,
            ),
        ),
    )

    derived = json.loads(
        await record_pipeline_step(pipeline_id="dependency-union-derived", op="init")
    )
    requested = json.loads(
        await record_pipeline_step(
            pipeline_id="dependency-union-requested",
            op="init",
            dependencies={"review_approach": ["plan"]},
        )
    )

    assert derived["success"] is True
    assert derived["dependencies"] == {"review_approach": ["rectify"]}
    assert requested["success"] is True
    assert requested["dependencies"] == {"review_approach": ["rectify", "plan"]}
    assert requested["dependency_count"] == len(requested["dependencies"])


@pytest.mark.anyio
async def test_kitchen_auto_init_tracker_can_receive_completion_credit(
    tool_ctx_kitchen_open,
    monkeypatch,
    tmp_path: Path,
) -> None:
    from autoskillit.server._tracker_authority import (
        _completion_tracker_binding,
        _release_kitchen_tracker_authority,
    )
    from autoskillit.server.tools.tools_kitchen._open_kitchen._tracker_auto_init import (
        _auto_init_pipeline_tracker,
    )
    from tests.conftest import bind_test_kitchen_identity

    monkeypatch.chdir(tmp_path)
    tool_ctx_kitchen_open.project_dir = tmp_path
    bind_test_kitchen_identity(tool_ctx_kitchen_open, "kitchen-admission-credit")
    _setup_project(tmp_path, tool_ctx_kitchen_open)
    kitchen_id = tool_ctx_kitchen_open.kitchen_id
    try:
        assert _auto_init_pipeline_tracker(tool_ctx_kitchen_open) is None
        target = tool_ctx_kitchen_open.kitchen_tracker_key.target
        tracker_path = _tracker_path(tmp_path, kitchen_id)
        tracker = json.loads(tracker_path.read_text())
        incarnation_id = tracker.get("tracker_incarnation_id")

        assert isinstance(incarnation_id, str) and incarnation_id
        assert tracker["dependencies"] == {"review_approach": ["rectify"]}
        kitchen_target, denial = _dependency_gate(tool_ctx_kitchen_open, "", "review_approach")
        assert kitchen_target is not None
        assert kitchen_target.path == target.path
        assert denial is not None
        assert "DEPENDENCY UNMET" in denial

        binding = _completion_tracker_binding(
            tool_ctx_kitchen_open,
            "",
            tracker_target=target,
        )
        assert len(binding) == 4
        assert binding[-1] == incarnation_id

        _grant_success_credit(tool_ctx_kitchen_open, tmp_path, "rectify", pipeline_id=kitchen_id)
        completed = json.loads(
            await record_pipeline_step(
                pipeline_id=kitchen_id,
                op="complete",
                step_name="rectify",
            )
        )
        assert completed["success"] is True
        tracker = json.loads(tracker_path.read_text())
        assert tracker["steps"]["rectify"]["status"] == "complete"

        refreshed_target, admitted = _dependency_gate(
            tool_ctx_kitchen_open,
            "",
            "review_approach",
        )
        assert refreshed_target is not None
        assert refreshed_target.path == target.path
        assert admitted is None

        assert _auto_init_pipeline_tracker(tool_ctx_kitchen_open) is None
        reinitialized = json.loads(tracker_path.read_text())
        assert reinitialized["tracker_incarnation_id"] == incarnation_id
        assert reinitialized["steps"]["rectify"]["status"] == "complete"
    finally:
        if tool_ctx_kitchen_open.kitchen_tracker_key is not None:
            _release_kitchen_tracker_authority(
                tool_ctx_kitchen_open,
                unregister=False,
                retire=False,
            )


def test_record_pipeline_step_docstring_pins_init_admission_guidance() -> None:
    docstring = (record_pipeline_step.__doc__ or "").casefold()
    docstring = re.sub(r"\s+", " ", docstring)

    assert "provides the dependency graph" not in docstring
    assert "idempotent" not in docstring
    assert "create-only" in docstring
    assert "run_skill" in docstring
    assert "optional" in docstring
    _, dependency_guidance = docstring.split("preflight:pipeline_tracker_dependencies", 1)
    dependency_guidance, initialization_guidance = dependency_guidance.split(
        "pipeline_tracker:init", 1
    )
    initialization_guidance = initialization_guidance.split('**op="status"**', 1)[0]
    assert dependency_guidance
    assert any(word in dependency_guidance for word in ("revis", "omit"))
    assert "supplied" in dependency_guidance
    assert any(word in dependency_guidance for word in ("graph", "mapping", "dependencies"))
    assert initialization_guidance
    assert "diagnos" in initialization_guidance
    assert "initialization failure" in initialization_guidance
    assert "before retrying" in initialization_guidance
