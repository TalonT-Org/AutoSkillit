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

Delivery-half status by parameter, stated plainly so a green run is not
read as more than it is:

- ``stale_threshold``, ``idle_output_timeout``, ``step_provider`` — covered
  and passing, against research.yaml's ``download_data``.
- ``model`` — bridged ``xfail(strict=True)`` on #4775, an independent
  pre-existing defect (``dispatch_items`` is compiled into ``mcp_kwargs``
  but filtered out of the runtime actual-kwargs assembly, so no fan-out
  step can pass attestation). Every bundled recipe step carrying a literal
  non-templated ``model:`` is a ``dispatch_items`` fan-out step, so there is
  no unblocked vehicle. It is bridged rather than rewritten against the
  unattested path: proving ``model`` delivery where the gate never engages
  is what let #4707 through in the first place, and would be no evidence at
  all here.
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


def _write_tracker_for(ready) -> None:
    from tests.server._pipeline_test_helpers import _write_tracker

    # Read the step identity from the fixture, not from with_args: a step need
    # not declare step_name in its with: block (planner's fan-out steps do not),
    # and ready.step_name is the attested identity in every case.
    step_name = ready.step_name
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
    _write_tracker_for(ready)

    result = json.loads(
        await run_skill(
            with_args["skill_command"],
            str(work_dir),
            step_name=ready.step_name,
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
    _write_tracker_for(ready)

    result = json.loads(
        await run_skill(
            with_args["skill_command"],
            str(work_dir),
            step_name=ready.step_name,
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
    _write_tracker_for(ready)

    result = json.loads(
        await run_skill(
            with_args["skill_command"],
            str(work_dir),
            step_name=ready.step_name,
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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Blocked by #4775: dispatch_items is filtered out of the runtime "
        "actual-kwargs assembly (handler_parameter=False) but not out of the "
        "compile-time mcp_kwargs binding, and _binding.py:1028 demands every "
        "compiled kwarg be present — so no fan-out step can pass attestation. "
        "Every bundled recipe step with a literal non-templated model: is a "
        "dispatch_items fan-out step, so this is the only reachable vehicle. "
        "Independent of #4707; goes green automatically once #4775 lands."
    ),
)
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
    concretely reachable #4707 trigger from a shipped compiled recipe (R2).

    This is the delivery half for ``model`` itself, the parameter #4707 was
    actually about. It is written against the *attested* path deliberately:
    proving model delivery on the unattested path instead (as
    test_run_skill_execution_tuning_fallbacks.py does, where the gate never
    engages) would reproduce the exact coverage gap this plan exists to
    close — a green test that is no evidence at all about attestation.

    So it is bridged rather than weakened. See the xfail reason for why it
    cannot pass today; the assertions below are the real contract and are
    what will be checked once #4775 is fixed.
    """
    from autoskillit.server.tools.tools_execution import run_skill
    from tests.fakes import InMemoryHeadlessExecutor

    ready = tool_ctx_ready_recipe
    executor = InMemoryHeadlessExecutor()
    ready.tool_ctx.executor = executor
    with_args = ready.with_args
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    _write_tracker_for(ready)

    # elaborate_phases declares no skill_inputs: sub-key — its child inputs come
    # from parsing skill_command's placeholders. Omitting the parameter lets the
    # runtime binder derive them (_binding.py:965-990) rather than guessing keys.
    declared_inputs = with_args.get("skill_inputs")
    optional_inputs = (
        {"skill_inputs": {name: "probe value" for name in declared_inputs}}
        if declared_inputs
        else {}
    )

    result = json.loads(
        await run_skill(
            with_args["skill_command"],
            str(work_dir),
            step_name=ready.step_name,
            output_dir=with_args["output_dir"],
            recipe_execution_id=ready.credential["execution_id"],
            invocation_template_digest=ready.template_digest,
            **optional_inputs,
        )
    )

    assert result.get("stage") != "preflight:recipe_execution", result
    assert len(executor.calls) == 1
    assert executor.calls[0].model == "opus[1m]"
