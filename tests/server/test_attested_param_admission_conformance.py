"""Attested admission and delivery for run_skill execution-tuning parameters."""

from __future__ import annotations

import json

import pytest

from tests._run_skill_caller_oracle import (
    ORCHESTRATOR_SAMPLES,
    build_run_skill_payload,
    run_skill_actual_mcp_kwargs,
)

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


async def test_attested_call_without_step_name_is_denied(tool_ctx_ready_recipe, tmp_path) -> None:
    from autoskillit.server.tools.tools_execution import run_skill
    from tests.fakes import InMemoryHeadlessExecutor

    ready = tool_ctx_ready_recipe
    executor = InMemoryHeadlessExecutor()
    ready.tool_ctx.executor = executor
    result = json.loads(
        await run_skill(
            ready.with_args["skill_command"],
            str(tmp_path),
            recipe_execution_id=ready.credential["execution_id"],
            invocation_template_digest=ready.template_digest,
        )
    )

    assert result["stage"] == "preflight:recipe_execution"
    assert "recipe_execution_step_missing" in json.dumps(result)
    assert not executor.calls


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
    monkeypatch,
) -> None:
    """research.yaml's download_data declares provider: anthropic (a bundled
    recipe purpose-added for this coverage — verified zero bundled recipe
    declared a step-level provider: before this). Not sent by the caller as
    step_provider; the call must still succeed (never denied), proving the
    with: mapped field (provider), not the parameter name (step_provider),
    is what a recipe author writes."""
    from autoskillit.server.tools.tools_execution import _run_skill_prepare, run_skill
    from tests.fakes import InMemoryHeadlessExecutor

    resolved_step_providers: list[str] = []
    resolve_candidate_policy = _run_skill_prepare.resolve_candidate_policy

    def record_step_provider(*args, **kwargs):
        resolved_step_providers.append(kwargs.get("step_provider", ""))
        return resolve_candidate_policy(*args, **kwargs)

    monkeypatch.setattr(_run_skill_prepare, "resolve_candidate_policy", record_step_provider)

    ready = tool_ctx_ready_recipe
    monkeypatch.setitem(ready.tool_ctx.config.features, "providers", True)
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
    assert resolved_step_providers == ["anthropic"]


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

    The assertions below check that the model declared by the recipe reaches
    the headless executor through the attested admission path.
    """
    from autoskillit.server.recipe._recipe_execution import (
        bind_attested_runtime_invocation,
        get_recipe_execution,
    )
    from autoskillit.server.tools.tools_execution import run_skill
    from tests.fakes import InMemoryHeadlessExecutor

    ready = tool_ctx_ready_recipe
    executor = InMemoryHeadlessExecutor()
    ready.tool_ctx.executor = executor
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    _write_tracker_for(ready)

    samples = {
        **ORCHESTRATOR_SAMPLES,
        "context": {
            **ORCHESTRATOR_SAMPLES["context"],
            "planner_dir": ".autoskillit/temp/planner/run-test",
        },
    }
    payload = build_run_skill_payload(
        ready.step_name,
        ready.step_body,
        ready.credential,
        cwd=str(work_dir),
        samples=samples,
    )
    installed = get_recipe_execution(ready.tool_ctx)
    assert installed is not None
    bound_inputs, _ = bind_attested_runtime_invocation(
        installed,
        execution_id=payload["recipe_execution_id"],
        step_name=payload["step_name"],
        template_digest=payload["invocation_template_digest"],
        skill_command=payload["skill_command"],
        skill_inputs=payload.get("skill_inputs"),
        actual_mcp_kwargs=run_skill_actual_mcp_kwargs(payload),
    )
    assert ORCHESTRATOR_SAMPLES["item"]["phase_id"] in dict(bound_inputs).values()

    result = json.loads(await run_skill(**payload))

    assert result.get("stage") != "preflight:recipe_execution", result
    assert len(executor.calls) == 1, result
    assert executor.calls[0].model == "opus[1m]"
