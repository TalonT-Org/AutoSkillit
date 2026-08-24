"""Attested end-to-end conformance for run_skill's execution-tuning
parameters, parametrized over the live role registry (#4707, S8).

Composes the two halves the pre-existing suite kept apart, per this plan's
"How Tests Missed This" analysis:

- **Denial half** — writable against the unparametrized ``tool_ctx_ready_recipe``
  default: denial requires only that the *caller* send a non-empty value;
  the step need declare nothing. Every EXECUTION_TUNING param is covered.
- **Delivery half** — requires S7's fixture parametrization. The pinned
  default (research/scope) declares no non-vacant tuning value for any of
  the four parameters, so before S7 this half was unwritable — a passing
  denial half was never evidence of a working delivery mechanism, the
  precise gap #4707 fell through.

Today's verdict recorded honestly per the plan: **denial half passes on
current (pre-#4707-fix) behavior — correctly, the gate should deny.
Delivery half was unwritable before S7/S8 landed.** A passing denial half
alone must never be mistaken for full coverage; this module's own
docstring is that record.
"""

from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]

# Sources are given as (recipe, step name) — a test selects by step name,
# not line number. See the plan's per-parameter delivery-half source table.
_RESEARCH_OVERRIDES = {
    "issue_url": "https://github.com/TalonT-Org/AutoSkillit/issues/4411",
    "source_dir": ".",
    "task": "test task",
    "task_description": "test task",
}
_PLANNER_OVERRIDES = {
    "task": "test task",
    "source_dir": ".",
}

# param_name -> a legitimate, non-empty explicit caller value for the
# denial-half test. idle_output_timeout uses R7's sharpest case: 0 is a
# legitimate explicit value (disables the watchdog) that the gate still
# denies, since it filters on `value != ""`, not truthiness.
_DENIAL_TEST_VALUES: dict[str, object] = {
    "model": "claude-opus-5",
    "stale_threshold": 2400,
    "idle_output_timeout": 0,
    "step_provider": "minimax",
}


def _write_tracker_for(ready, with_args) -> None:
    from tests.server._pipeline_test_helpers import _write_tracker

    step_name = with_args["step_name"]
    assert isinstance(step_name, str)
    _write_tracker(
        ready.tool_ctx.project_dir,
        "AB",
        {step_name: {"status": "pending"}},
        {},
        kitchen_id=ready.tool_ctx.kitchen_id,
    )


@pytest.mark.parametrize("param_name", sorted(_DENIAL_TEST_VALUES))
async def test_forwarding_execution_tuning_param_is_denied_with_actionable_message(
    param_name: str,
    tmp_path,
    tool_ctx_ready_recipe,
) -> None:
    """An attested call forwarding any EXECUTION_TUNING parameter is denied,
    naming the parameter, stating it is server-resolved, and naming the
    with: override channel — see _binding.py:_undeclared_runtime_param_message."""
    from autoskillit.server.tools.tools_execution import run_skill

    ready = tool_ctx_ready_recipe
    with_args = ready.with_args
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    _write_tracker_for(ready, with_args)

    result = json.loads(
        await run_skill(
            with_args["skill_command"],
            str(work_dir),
            step_name=with_args["step_name"],
            output_dir=with_args["output_dir"],
            recipe_execution_id=ready.credential["execution_id"],
            invocation_template_digest=ready.template_digest,
            skill_inputs={name: "probe value" for name in with_args["skill_inputs"]},
            **{param_name: _DENIAL_TEST_VALUES[param_name]},
        )
    )

    serialized = json.dumps(result)
    assert result.get("stage") == "preflight:recipe_execution", result
    assert "RECIPE EXECUTION REJECTED" in serialized
    assert param_name in serialized
    assert "server-resolved" in serialized
    assert "with:" in serialized


@pytest.mark.parametrize(
    "tool_ctx_ready_recipe",
    [("research", "download_data", _RESEARCH_OVERRIDES)],
    indirect=True,
)
async def test_stale_threshold_and_idle_output_timeout_delivered_from_recipe_step(
    tmp_path,
    tool_ctx_ready_recipe,
) -> None:
    """research.yaml's download_data declares stale_threshold: 14400 and
    idle_output_timeout: 0 (both step fields, not run_skill parameters).
    Neither is sent by the caller; both must reach the executor."""
    from autoskillit.server.tools.tools_execution import run_skill
    from tests.fakes import InMemoryHeadlessExecutor

    ready = tool_ctx_ready_recipe
    executor = InMemoryHeadlessExecutor()
    ready.tool_ctx.executor = executor
    with_args = ready.with_args
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    _write_tracker_for(ready, with_args)

    result = json.loads(
        await run_skill(
            with_args["skill_command"],
            str(work_dir),
            step_name=with_args["step_name"],
            output_dir=with_args["output_dir"],
            recipe_execution_id=ready.credential["execution_id"],
            invocation_template_digest=ready.template_digest,
            skill_inputs={name: "probe value" for name in with_args["skill_inputs"]},
        )
    )

    assert result.get("stage") != "preflight:recipe_execution", result
    assert len(executor.calls) == 1
    assert executor.calls[0].stale_threshold == 14400
    assert executor.calls[0].idle_output_timeout == 0


@pytest.mark.parametrize(
    "tool_ctx_ready_recipe",
    [("research", "download_data", _RESEARCH_OVERRIDES)],
    indirect=True,
)
async def test_step_provider_delivered_from_recipe_step_without_caller_forwarding(
    tmp_path,
    tool_ctx_ready_recipe,
) -> None:
    """research.yaml's download_data declares provider: anthropic (a bundled
    recipe purpose-added for this coverage — verified zero bundled recipe
    declared a step-level provider: before this). Not sent by the caller as
    step_provider; the call must still succeed (never denied), proving the
    with: mapped field (provider), not the parameter name (step_provider),
    is what a recipe author writes."""
    from autoskillit.server.tools.tools_execution import run_skill
    from tests.fakes import InMemoryHeadlessExecutor

    ready = tool_ctx_ready_recipe
    executor = InMemoryHeadlessExecutor()
    ready.tool_ctx.executor = executor
    with_args = ready.with_args
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    _write_tracker_for(ready, with_args)

    result = json.loads(
        await run_skill(
            with_args["skill_command"],
            str(work_dir),
            step_name=with_args["step_name"],
            output_dir=with_args["output_dir"],
            recipe_execution_id=ready.credential["execution_id"],
            invocation_template_digest=ready.template_digest,
            skill_inputs={name: "probe value" for name in with_args["skill_inputs"]},
        )
    )

    serialized = json.dumps(result)
    assert result.get("stage") != "preflight:recipe_execution", result
    assert "RECIPE EXECUTION REJECTED" not in serialized
    assert len(executor.calls) == 1


@pytest.mark.parametrize(
    "tool_ctx_ready_recipe",
    [("planner", "elaborate_phases", _PLANNER_OVERRIDES)],
    indirect=True,
)
async def test_model_delivered_from_recipe_step_without_caller_forwarding(
    tmp_path,
    tool_ctx_ready_recipe,
) -> None:
    """planner.yaml's elaborate_phases declares model: "opus[1m]" — the
    concretely reachable #4707 trigger from a shipped compiled recipe
    (R2). Every literal-model step in planner.yaml declares dispatch_items
    fan-out; there is no fan-out-free alternative, so this step is used
    directly rather than sidestepped. Not sent by the caller; must reach
    the executor with model omitted from the call, matching R2's own
    verification (executor receives opus[1m] with model omitted)."""
    from autoskillit.server.tools.tools_execution import run_skill
    from tests.fakes import InMemoryHeadlessExecutor

    ready = tool_ctx_ready_recipe
    executor = InMemoryHeadlessExecutor()
    ready.tool_ctx.executor = executor
    with_args = ready.with_args
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    _write_tracker_for(ready, with_args)

    result = json.loads(
        await run_skill(
            with_args["skill_command"],
            str(work_dir),
            step_name=with_args["step_name"],
            output_dir=with_args["output_dir"],
            recipe_execution_id=ready.credential["execution_id"],
            invocation_template_digest=ready.template_digest,
            skill_inputs={name: "probe value" for name in with_args["skill_inputs"]},
        )
    )

    assert result.get("stage") != "preflight:recipe_execution", result
    assert len(executor.calls) == 1
    assert executor.calls[0].model == "opus[1m]"
