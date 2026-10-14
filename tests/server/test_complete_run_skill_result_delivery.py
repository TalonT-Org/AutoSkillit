"""The completion handler returns a step identity for automatic segment delivery."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]

_IMPLEMENTATION_OVERRIDES = {
    "issue_url": "https://github.com/TalonT-Org/AutoSkillit/issues/5256",
    "task": "test task",
}


def _publish_receipt(tool_ctx, *, success: bool) -> str:
    authority = tool_ctx.run_skill_completion
    assert authority is not None
    invocation_id = authority.begin(
        kitchen_id=tool_ctx.kitchen_id,
        request_session_id="request-session",
        tracker_order_id="",
        tracker_path="",
        tracker_kitchen_id="",
        tracker_incarnation_id="",
        step_name="plan",
    )
    receipt = authority.draft(
        invocation_id,
        classification="success" if success else "timeout",
        success=success,
        result_digest="sha256:completion-result",
    )
    return authority.publish(receipt.receipt_id).receipt_id


@pytest.mark.parametrize(
    "tool_ctx_ready_recipe",
    [pytest.param(("implementation", "plan", _IMPLEMENTATION_OVERRIDES), id="implementation")],
    indirect=True,
)
@pytest.mark.parametrize(
    ("run_skill_success", "expected_segment"),
    [(True, "success"), (False, "recovery")],
    ids=("success", "failure"),
)
async def test_completion_receipt_delivery_carries_plan_step_name(
    tool_ctx_ready_recipe,
    tmp_path,
    run_skill_success: bool,
    expected_segment: str,
) -> None:
    from autoskillit.server.tools.tools_pipeline_tracker import complete_run_skill_result
    from tests.server._recipe_segment_test_helpers import (
        assert_delivery_outcome,
        assert_real_checkpoint_response_budget,
    )

    receipt_id = _publish_receipt(tool_ctx_ready_recipe.tool_ctx, success=run_skill_success)
    response = json.loads(
        await complete_run_skill_result(
            receipt_id,
            ctx=SimpleNamespace(session_id="request-session"),
        )
    )

    assert response["success"] is True
    assert response["step_name"] == "plan"
    assert response["run_skill_success"] is run_skill_success
    assert_delivery_outcome("complete_run_skill_result", response, expected=run_skill_success)
    assert response["recipe_segment"]["kind"] == expected_segment
    if run_skill_success:
        await assert_real_checkpoint_response_budget(
            tool_ctx_ready_recipe,
            "complete_run_skill_result",
            response,
            tmp_path,
        )
