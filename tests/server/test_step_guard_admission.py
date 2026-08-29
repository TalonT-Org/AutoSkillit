"""Acceptance coverage for server-authoritative recipe step guards."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from typing import get_type_hints
from unittest.mock import AsyncMock, Mock

import pytest

from autoskillit.core import compute_recipe_execution_snapshot_digest
from autoskillit.pipeline import ReadyRecipe
from autoskillit.server.tools.tools_execution._gates import _check_pipeline_deps
from tests.server._pipeline_test_helpers import _write_tracker

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]

_GUARDED_RECIPE = (
    "research-design",
    "apply",
    {
        "issue_url": "https://github.com/TalonT-Org/AutoSkillit/issues/4497",
        "source_dir": ".",
        "task": "test guarded recipe admission",
    },
)


def _write_guard_tracker(ready, *, dependencies: dict[str, list[str]] | None = None) -> None:
    _write_tracker(
        ready.tool_ctx.project_dir,
        ready.tool_ctx.kitchen_id,
        {
            "apply": {"status": "pending"},
            "synthesize": {"status": "pending"},
        },
        dependencies or {},
        kitchen_id=ready.tool_ctx.kitchen_id,
    )


def _tracker_data(ready) -> dict[str, object]:
    tracker = (
        ready.tool_ctx.project_dir
        / ".autoskillit"
        / "temp"
        / "pipeline_tracker"
        / f"{ready.tool_ctx.kitchen_id}.json"
    )
    return json.loads(tracker.read_text())


def _run_kwargs(ready, work_dir, **overrides: object) -> dict[str, object]:
    with_args = ready.with_args
    declared_inputs = with_args.get("skill_inputs", {})
    assert isinstance(declared_inputs, dict)
    kwargs: dict[str, object] = {
        "skill_command": with_args["skill_command"],
        "cwd": str(work_dir),
        "step_name": ready.step_name,
        "output_dir": with_args["output_dir"],
        "recipe_execution_id": ready.credential["execution_id"],
        "invocation_template_digest": ready.template_digest,
        "skill_inputs": {name: "test value" for name in declared_inputs},
    }
    kwargs.update(overrides)
    return kwargs


def _deny_tokens(result: str) -> str:
    payload = json.loads(result)
    return json.dumps(payload, sort_keys=True)


def test_run_skill_declares_boolean_guard_values() -> None:
    from autoskillit.server.tools.tools_execution._run_skill_dispatch import run_skill

    annotation = get_type_hints(run_skill)["step_guard_value"]
    assert annotation == str | bool | None


def _replace_snapshot(
    snapshot, *, templates=None, dynamic_skill_step_names=None, step_guards=None
):
    templates = dict(snapshot.templates) if templates is None else dict(templates)
    dynamic_skill_step_names = (
        snapshot.dynamic_skill_step_names
        if dynamic_skill_step_names is None
        else frozenset(dynamic_skill_step_names)
    )
    step_guards = dict(snapshot.step_guards) if step_guards is None else dict(step_guards)
    return replace(
        snapshot,
        templates=templates,
        dynamic_skill_step_names=dynamic_skill_step_names,
        step_guards=step_guards,
        snapshot_digest=compute_recipe_execution_snapshot_digest(
            execution_id=snapshot.execution_id,
            recipe_name=snapshot.recipe_name,
            content_hash=snapshot.content_hash,
            composite_hash=snapshot.composite_hash,
            templates=templates,
            dynamic_skill_step_names=dynamic_skill_step_names,
            step_guards=step_guards,
        ),
    )


@pytest.mark.parametrize("tool_ctx_ready_recipe", [_GUARDED_RECIPE], indirect=True)
async def test_truthy_guard_bypasses_dispatch_marks_tracker_and_unblocks_dependents(
    tmp_path,
    tool_ctx_ready_recipe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.server.tools import tools_execution

    ready = tool_ctx_ready_recipe
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    _write_guard_tracker(ready, dependencies={"synthesize": ["apply"]})
    backend = AsyncMock(side_effect=AssertionError("guarded step reached backend preparation"))
    session = Mock(side_effect=AssertionError("guarded step reached session preparation"))
    monkeypatch.setattr(tools_execution, "_prepare_dispatch_backend", backend)
    monkeypatch.setattr(tools_execution, "_prepare_dispatch_session", session)

    first = json.loads(
        await tools_execution.run_skill(**_run_kwargs(ready, work_dir, step_guard_value="true"))
    )
    second = json.loads(
        await tools_execution.run_skill(**_run_kwargs(ready, work_dir, step_guard_value="true"))
    )

    assert first == {
        "next_step": "synthesize",
        "reason": "skip_when_true",
        "skipped": True,
        "step_name": "apply",
        "success": True,
    }
    assert second == first
    assert backend.await_count == 0
    assert session.call_count == 0
    tracker = _tracker_data(ready)
    assert tracker["steps"]["apply"]["status"] == "skipped"
    authority = SimpleNamespace(
        data=tracker,
        error=None,
        target_order_id=ready.tool_ctx.kitchen_id,
    )
    assert _check_pipeline_deps("synthesize", authority) is None


@pytest.mark.parametrize("tool_ctx_ready_recipe", [_GUARDED_RECIPE], indirect=True)
async def test_guard_precedes_dynamic_recipe_skill_branch(
    tmp_path,
    tool_ctx_ready_recipe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.server.tools import tools_execution

    ready = tool_ctx_ready_recipe
    state = ready.tool_ctx.recipe_initialization_state
    assert isinstance(state, ReadyRecipe)
    installed = state.installed_execution
    templates = dict(installed.snapshot.templates)
    templates.pop(ready.step_name)
    dynamic_snapshot = _replace_snapshot(
        installed.snapshot,
        templates=templates,
        dynamic_skill_step_names=installed.snapshot.dynamic_skill_step_names | {ready.step_name},
    )
    monkeypatch.setattr(
        ready.tool_ctx,
        "recipe_initialization_state",
        replace(state, installed_execution=replace(installed, snapshot=dynamic_snapshot)),
    )
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    _write_guard_tracker(ready)
    backend = AsyncMock(side_effect=AssertionError("dynamic guarded step reached backend"))
    monkeypatch.setattr(tools_execution, "_prepare_dispatch_backend", backend)

    result = json.loads(
        await tools_execution.run_skill(**_run_kwargs(ready, work_dir, step_guard_value="true"))
    )

    assert result["success"] is True
    assert result["skipped"] is True
    assert result["next_step"] == "synthesize"


@pytest.mark.parametrize("tool_ctx_ready_recipe", [_GUARDED_RECIPE], indirect=True)
async def test_guard_requires_verified_attestation_before_tracker_mutation(
    tmp_path,
    tool_ctx_ready_recipe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.server.tools import tools_execution

    ready = tool_ctx_ready_recipe
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    _write_guard_tracker(ready)
    backend = AsyncMock(side_effect=AssertionError("unattested guard reached backend"))
    monkeypatch.setattr(tools_execution, "_prepare_dispatch_backend", backend)

    result = await tools_execution.run_skill(
        **_run_kwargs(
            ready,
            work_dir,
            recipe_execution_id="",
            invocation_template_digest="",
            step_guard_value="true",
        )
    )

    assert "recipe_execution_attestation_missing" in _deny_tokens(result)
    assert _tracker_data(ready)["steps"]["apply"]["status"] == "pending"
    assert backend.await_count == 0
    assert backend.await_count == 0


@pytest.mark.parametrize("tool_ctx_ready_recipe", [_GUARDED_RECIPE], indirect=True)
async def test_guard_requires_a_present_valid_captured_value(
    tmp_path,
    tool_ctx_ready_recipe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.server.tools import tools_execution

    ready = tool_ctx_ready_recipe
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    _write_guard_tracker(ready)
    backend = AsyncMock(side_effect=AssertionError("invalid guard value reached backend"))
    monkeypatch.setattr(tools_execution, "_prepare_dispatch_backend", backend)

    absent = await tools_execution.run_skill(**_run_kwargs(ready, work_dir))
    explicit_none = await tools_execution.run_skill(
        **_run_kwargs(ready, work_dir, step_guard_value=None)
    )
    invalid = [
        await tools_execution.run_skill(**_run_kwargs(ready, work_dir, step_guard_value=value))
        for value in ("maybe", "${{ context.is_silent_type }}", 1)
    ]

    assert "recipe_step_guard_value_required" in _deny_tokens(absent)
    assert "recipe_step_guard_value_required" in _deny_tokens(explicit_none)
    assert all("recipe_step_guard_value_invalid" in _deny_tokens(result) for result in invalid)
    assert all("'apply'" in result for result in [absent, explicit_none, *invalid])
    assert backend.await_count == 0


@pytest.mark.parametrize("tool_ctx_ready_recipe", [_GUARDED_RECIPE], indirect=True)
async def test_guard_value_for_an_unguarded_attested_step_is_denied(
    tmp_path,
    tool_ctx_ready_recipe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.server.tools import tools_execution

    ready = tool_ctx_ready_recipe
    state = ready.tool_ctx.recipe_initialization_state
    assert isinstance(state, ReadyRecipe)
    installed = state.installed_execution
    monkeypatch.setattr(
        ready.tool_ctx,
        "recipe_initialization_state",
        replace(
            state,
            installed_execution=replace(
                installed,
                snapshot=_replace_snapshot(installed.snapshot, step_guards={}),
            ),
        ),
    )
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    _write_guard_tracker(ready)
    backend = AsyncMock(side_effect=AssertionError("unguarded value reached backend"))
    monkeypatch.setattr(tools_execution, "_prepare_dispatch_backend", backend)

    result = await tools_execution.run_skill(
        **_run_kwargs(ready, work_dir, step_guard_value="true")
    )

    assert "recipe_step_guard_unexpected" in _deny_tokens(result)
    assert "'apply'" in result
    assert backend.await_count == 0


@pytest.mark.parametrize("tool_ctx_ready_recipe", [_GUARDED_RECIPE], indirect=True)
async def test_falsy_guard_and_unguarded_calls_reach_normal_admission(
    tmp_path,
    tool_ctx_ready_recipe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.server.tools import tools_execution

    ready = tool_ctx_ready_recipe
    state = ready.tool_ctx.recipe_initialization_state
    assert isinstance(state, ReadyRecipe)
    installed = state.installed_execution
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    _write_guard_tracker(ready)
    backend = AsyncMock(return_value='{"dispatched":true,"success":true}')
    monkeypatch.setattr(tools_execution, "_prepare_dispatch_backend", backend)

    falsy = json.loads(
        await tools_execution.run_skill(**_run_kwargs(ready, work_dir, step_guard_value="false"))
    )
    monkeypatch.setattr(
        ready.tool_ctx,
        "recipe_initialization_state",
        replace(
            state,
            installed_execution=replace(
                installed,
                snapshot=_replace_snapshot(installed.snapshot, step_guards={}),
            ),
        ),
    )
    unguarded = json.loads(await tools_execution.run_skill(**_run_kwargs(ready, work_dir)))

    assert falsy == {"dispatched": True, "success": True}
    assert unguarded == falsy
    assert backend.await_count == 2
