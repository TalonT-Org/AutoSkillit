"""Attested bundled dispatches of BOUNDED skills reach the executor inside their own scope."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]

_IMPLEMENTATION_OVERRIDES = {
    "issue_url": "https://github.com/TalonT-Org/AutoSkillit/issues/5256",
    "task": "test task",
}


@pytest.mark.parametrize(
    ("tool_ctx_ready_recipe", "skill"),
    [
        (("implementation", "plan", _IMPLEMENTATION_OVERRIDES), "make-plan"),
        (("implementation", "prepare_pr", _IMPLEMENTATION_OVERRIDES), "prepare-pr"),
    ],
    indirect=["tool_ctx_ready_recipe"],
)
async def test_attested_bounded_dispatch_is_admitted_inside_the_skill_scope(
    tool_ctx_ready_recipe, git_checkout: Path, skill: str
) -> None:
    from autoskillit.server.tools.tools_execution import run_skill
    from tests.fakes import InMemoryHeadlessExecutor
    from tests.server._helpers import _ready_recipe_segment_step
    from tests.server._pipeline_test_helpers import _write_tracker

    ready = tool_ctx_ready_recipe
    executor = InMemoryHeadlessExecutor()
    ready.tool_ctx.executor = executor
    step, credential = _ready_recipe_segment_step(ready.tool_ctx, ready.step_name)
    with_args = step["with"]
    _write_tracker(
        ready.tool_ctx.project_dir,
        "AB",
        {ready.step_name: {"status": "pending"}},
        {},
        kitchen_id=ready.tool_ctx.kitchen_id,
    )

    result = json.loads(
        await run_skill(
            skill_command=with_args["skill_command"],
            cwd=str(git_checkout),
            step_name=ready.step_name,
            output_dir=with_args.get("output_dir", ""),
            recipe_execution_id=credential["execution_id"],
            invocation_template_digest=credential["invocation_template_digests"][ready.step_name],
            skill_inputs={name: "probe value" for name in with_args["skill_inputs"]},
        )
    )

    assert result.get("stage") != "validate_args:run_skill", result
    assert "outside the declared write scope" not in json.dumps(result)
    assert len(executor.calls) == 1, result
    watch_dirs = {Path(entry) for entry in executor.calls[0].write_watch_dirs}
    checkout = git_checkout.resolve()
    assert checkout / ".autoskillit" / "temp" / skill in watch_dirs
    assert checkout not in watch_dirs
