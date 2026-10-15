"""The implementation plan path carries attestation through bind_plan_set to verify."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from tests.server._pipeline_test_helpers import _ack_direct_run_skill_result
from tests.server._recipe_fixture_parameters import (
    IMPLEMENTATION_OVERRIDES,
    IMPLEMENTATION_REVIEW_READY_PARAMETERS,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]

_INPUT_REFERENCE = re.compile(r"\$\{\{\s*(inputs|context)\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_INPUT_DEFAULTS = {"adversarial_review_level": "auto"}


def _plan_output_token(result_text: str, name: str) -> str:
    match = re.search(rf"(?m)^{re.escape(name)}\s*=\s*(.+?)\s*$", result_text)
    assert match is not None, result_text
    return match.group(1)


def _skill_inputs(
    step_name: str,
    step_body: dict[str, Any],
    credential: dict[str, Any],
    ingredient_values: dict[str, str],
    context_values: dict[str, str],
) -> dict[str, str | int | bool]:
    shape = credential["skill_input_shapes"][step_name]
    declared = step_body["with"]["skill_inputs"]
    absence_values = shape["absence_values"]
    values: dict[str, str | int | bool] = {}
    for key in shape["keys"]:
        value = declared[key]
        if not isinstance(value, str):
            values[key] = value
            continue
        match = _INPUT_REFERENCE.fullmatch(value)
        if match is None:
            values[key] = value
            continue
        source, name = match.groups()
        if source == "inputs":
            try:
                values[key] = (
                    ingredient_values[name] if name in ingredient_values else _INPUT_DEFAULTS[name]
                )
            except KeyError as exc:
                raise KeyError(
                    f"Step {step_name!r}, skill input {key!r}: "
                    f"no value or default for inputs.{name}"
                ) from exc
        else:
            values[key] = context_values[name] if name in context_values else absence_values[key]
    return values


async def _run_step(
    ready,
    git_checkout: Path,
    step_name: str,
    step_body: dict[str, Any],
    credential: dict[str, Any],
    ingredient_values: dict[str, str],
    context_values: dict[str, str],
) -> dict[str, Any]:
    from autoskillit.server.tools.tools_execution import run_skill

    with_args = step_body["with"]
    return json.loads(
        await run_skill(
            skill_command=with_args["skill_command"],
            cwd=str(git_checkout),
            step_name=step_name,
            output_dir=with_args.get("output_dir", ""),
            recipe_execution_id=credential["execution_id"],
            invocation_template_digest=credential["invocation_template_digests"][step_name],
            skill_inputs=_skill_inputs(
                step_name,
                step_body,
                credential,
                ingredient_values,
                context_values,
            ),
        )
    )


def _carrier_delivers_step(carrier: dict[str, Any], step_name: str) -> bool:
    if any(body.get("step") == step_name for body in carrier.get("bodies", [])):
        return True
    return any(
        step_name in closure.get("steps", []) for closure in carrier.get("pull_closures", [])
    )


@pytest.mark.parametrize(
    "tool_ctx_ready_recipe", IMPLEMENTATION_REVIEW_READY_PARAMETERS, indirect=True
)
async def test_plan_bind_verify_walk_uses_only_delivered_responses(
    tool_ctx_ready_recipe,
    git_checkout: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.core.io import load_yaml
    from tests.fakes import InMemoryHeadlessExecutor
    from tests.server._helpers import _skill_ok
    from tests.server._plan_set_test_helpers import write_plan_set_parts
    from tests.server._recipe_segment_test_helpers import assert_delivery_outcome

    ready = tool_ctx_ready_recipe
    credential = ready.receipt["recipe_execution"]
    _, part_a, part_b = write_plan_set_parts(git_checkout, ready.tool_ctx, monkeypatch)
    plan_text = f"verdict = plan\nplan_path = {part_a}\nplan_parts = {part_a},{part_b}"
    executor = InMemoryHeadlessExecutor(default_result=_skill_ok(plan_text))
    monkeypatch.setattr(ready.tool_ctx, "executor", executor)

    plan_response = await _run_step(
        ready,
        git_checkout,
        "plan",
        ready.step_body,
        credential,
        IMPLEMENTATION_OVERRIDES,
        {},
    )
    assert plan_response["success"] is True
    plan_result = plan_response["result"]
    assert isinstance(plan_result, str)
    plan_path = _plan_output_token(plan_result, "plan_path")
    plan_parts = _plan_output_token(plan_result, "plan_parts")
    assert _plan_output_token(plan_result, "verdict") == "plan"
    _ack_direct_run_skill_result(ready.tool_ctx, plan_response)

    from autoskillit.server.tools.tools_plan_set import bind_plan_set

    bind_response = json.loads(
        await bind_plan_set(
            step_name="bind_plan_set",
            plan_parts=plan_parts,
            cwd=str(git_checkout),
            issue_url=IMPLEMENTATION_OVERRIDES["issue_url"],
        )
    )
    assert bind_response["success"] is True, bind_response
    assert_delivery_outcome("bind_plan_set", bind_response, expected=True)
    assert bind_response["plan_set_state"] == "sealed"
    assert bind_response["plan_set_parts"] == "\n".join(plan_parts.split(","))
    authority_path = Path(bind_response["plan_set_authority_path"])
    assert authority_path.is_absolute() and authority_path.is_file()
    authority_digest = bind_response["plan_set_authority_digest"]
    assert isinstance(authority_digest, str)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", authority_digest)
    carrier = bind_response["recipe_segment"]
    assert carrier["kind"] == "success"
    assert _carrier_delivers_step(carrier, "verify")

    verify_record = next((body for body in carrier["bodies"] if body["step"] == "verify"), None)
    if verify_record is not None:
        verify_yaml = verify_record["body"]
        assert (
            verify_record["invocation_template_digest"]
            == credential["invocation_template_digests"]["verify"]
        )
    else:
        from autoskillit.server.tools.tools_recipe import get_recipe_section

        pull = {key: value for key, value in carrier["recipe_pull"].items() if key != "pull_tool"}
        chunks = []
        page_args = {"part": 0}
        while True:
            page = json.loads(await get_recipe_section(section="verify", **pull, **page_args))
            assert page["success"] is True, page
            assert page["payload_sha256"] == pull["payload_sha256"]
            chunks.append(page["content"])
            if not page["has_more"]:
                break
            page_args = {
                "part": page["next_part"],
                "page_plan_sha256": page["page_plan_sha256"],
                "continuation": page["continuation"],
            }
        verify_yaml = "".join(chunks)
    verify_body = load_yaml(verify_yaml)["verify"]
    verify_context = {
        **plan_response,
        "plan_path": plan_path,
        **bind_response,
    }
    verify_response = await _run_step(
        ready,
        git_checkout,
        "verify",
        verify_body,
        credential,
        IMPLEMENTATION_OVERRIDES,
        {key: value for key, value in verify_context.items() if isinstance(value, str)},
    )
    assert verify_response["success"] is True, verify_response
    serialized = json.dumps(verify_response)
    for code in (
        "recipe_execution_attestation_missing",
        "recipe_execution_id_mismatch",
        "recipe_execution_step_mismatch",
        "invocation_template_digest_mismatch",
    ):
        assert code not in serialized
    expected_command = verify_body["with"]["skill_command"].replace("/autoskillit:", "/", 1)
    assert executor.calls[-1].skill_command.splitlines()[0] == expected_command
    assert executor.calls[-1].step_name == "verify"
