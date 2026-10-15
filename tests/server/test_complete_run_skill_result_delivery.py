"""The completion handler returns a step identity for automatic segment delivery."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from autoskillit.pipeline import ToolContext
from tests.server._recipe_fixture_parameters import (
    IMPLEMENTATION_READY_PARAMETERS,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]

_REQUEST_SESSION_ID = "request-session"


def _publish_receipt(tool_ctx: ToolContext, *, success: bool) -> str:
    authority = tool_ctx.run_skill_completion
    assert authority is not None
    invocation_id = authority.begin(
        kitchen_id=tool_ctx.kitchen_id,
        request_session_id=_REQUEST_SESSION_ID,
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
    IMPLEMENTATION_READY_PARAMETERS,
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
            ctx=SimpleNamespace(session_id=_REQUEST_SESSION_ID),
        )
    )

    assert response["success"] is True
    assert response["step_name"] == "plan"
    assert response["run_skill_success"] is run_skill_success
    if not run_skill_success:
        assert_delivery_outcome("complete_run_skill_result", response, expected=False)
    assert response["recipe_segment"]["kind"] == expected_segment
    if not run_skill_success:
        carrier = response["recipe_segment"]
        assert carrier["source_step"] == "plan"
        assert "release_issue_failure" in carrier["target_steps"]
        assert "release_issue_failure" in carrier["pull_closure"]
        assert {"section": "release_issue_failure", "part": 0} in carrier["pull_requests"]
        assert carrier["recipe_pull"] == tool_ctx_ready_recipe.receipt["recipe_pull"]
    if run_skill_success:
        await assert_real_checkpoint_response_budget(
            tool_ctx_ready_recipe,
            "complete_run_skill_result",
            response,
            tmp_path,
        )
