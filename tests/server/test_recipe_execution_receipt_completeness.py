"""Completion receipts are the re-requestable source for attested step digests."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from autoskillit.core import (
    PACK_REGISTRY,
    FinalizedRecipeProjection,
    build_recipe_execution_credential,
    compute_runtime_binding_digest,
)
from autoskillit.recipe import get_skill_contract, load_bundled_manifest
from autoskillit.server.recipe._recipe_execution import (
    bind_attested_runtime_invocation,
    build_bound_child_prompt,
    get_recipe_execution,
)
from tests._run_skill_caller_oracle import (
    _SINGLE_BRACE_TOKEN,
    ORCHESTRATOR_SAMPLES,
    build_run_skill_payload,
    literal_run_skill_payload,
    run_skill_actual_mcp_kwargs,
)
from tests.server._recipe_fixture_parameters import (
    IMPLEMENTATION_OVERRIDES,
    IMPLEMENTATION_READY_PARAMETERS,
    RESEARCH_OVERRIDES,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_RECIPE_INPUT_OVERRIDES = {
    "full-audit": {"workspace": ".", "branch": "develop"},
    "implementation": IMPLEMENTATION_OVERRIDES,
    "implementation-groups": {"source_doc": "test source document", "source_dir": "."},
    "merge-prs": {"source_dir": "."},
    "planner": {"task": "test task", "source_dir": "."},
    "remediation": IMPLEMENTATION_OVERRIDES,
    "research": RESEARCH_OVERRIDES,
}


def _recipe_fixture_parameters() -> list[tuple[str, str, dict[str, str]]]:
    from autoskillit.recipe import load_and_validate
    from tests._tracked_recipes import delivery_recipe_names

    parameters = []
    for recipe_name in delivery_recipe_names(_PROJECT_ROOT):
        loaded = load_and_validate(
            recipe_name,
            project_dir=_PROJECT_ROOT,
            include_finalized_projection=True,
        )
        projection = loaded["_finalized_projection"]
        assert isinstance(projection, FinalizedRecipeProjection)
        attested_step = next(
            (
                step_name
                for step_name, invocation in projection.binding_projection.invocations.items()
                if invocation.tool_name == "run_skill" and invocation.attested
            ),
            None,
        )
        if attested_step is None:
            continue
        overrides = _RECIPE_INPUT_OVERRIDES[recipe_name]
        parameters.append((recipe_name, attested_step, overrides))
    return parameters


_RECIPE_FIXTURE_PARAMETERS = [
    pytest.param(parameters, id=parameters[0]) for parameters in _recipe_fixture_parameters()
]


@pytest.fixture(autouse=True)
def _enable_attested_skill_dependency_packs(tool_ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep dependency visibility from short-circuiting the credential admission tests."""
    monkeypatch.setattr(tool_ctx.config.packs, "enabled", list(PACK_REGISTRY))


def _skill_command(tool_ctx, step_name: str) -> str:
    installed = get_recipe_execution(tool_ctx)
    assert installed is not None
    template = installed.snapshot.templates[step_name]
    command = next(
        value.effective_value
        for value in template.invocation.mcp_kwargs
        if value.name == "skill_command"
    )
    assert isinstance(command, str)
    return command


async def _run_attested_call(
    ready,
    git_checkout: Path,
    step_name: str,
    execution_id: str,
    digest: str,
) -> dict[str, object]:
    from autoskillit.server.tools.tools_execution import run_skill

    return json.loads(
        await run_skill(
            skill_command=_skill_command(ready.tool_ctx, step_name),
            cwd=str(git_checkout),
            step_name=step_name,
            recipe_execution_id=execution_id,
            invocation_template_digest=digest,
        )
    )


def _assert_digest_recovery(
    response: dict[str, object], step_name: str, initialization_id: str, code: str
) -> str:
    assert response["success"] is False, response
    serialized = response["error"]
    assert isinstance(serialized, str)
    assert "Recovery:" in serialized
    assert f"[{code}]" in serialized
    assert f"step '{step_name}'" in serialized
    assert f'complete_recipe_initialization(initialization_id="{initialization_id}")' in serialized
    assert f'invocation_template_digests["{step_name}"]' in serialized
    match = re.search(
        r'complete_recipe_initialization\(initialization_id="([0-9a-f]+)"\)', serialized
    )
    assert match is not None, response
    return match.group(1)


@pytest.mark.parametrize("tool_ctx_ready_recipe", _RECIPE_FIXTURE_PARAMETERS, indirect=True)
async def test_completion_receipt_carries_every_attested_step_credential(
    tool_ctx_ready_recipe,
) -> None:
    ready = tool_ctx_ready_recipe
    installed = get_recipe_execution(ready.tool_ctx)
    assert installed is not None

    credential = ready.receipt["recipe_execution"]
    assert credential["invocation_template_digests"] == dict(installed.snapshot.template_digests)
    expected_shapes = build_recipe_execution_credential(installed.snapshot).as_wire_block()[
        "skill_input_shapes"
    ]
    assert credential["skill_input_shapes"] == expected_shapes


@pytest.mark.parametrize("tool_ctx_ready_recipe", _RECIPE_FIXTURE_PARAMETERS, indirect=True)
async def test_missing_digest_rejection_names_step_and_call(
    tool_ctx_ready_recipe, git_checkout: Path
) -> None:
    ready = tool_ctx_ready_recipe
    installed = get_recipe_execution(ready.tool_ctx)
    assert installed is not None

    for step_name in installed.snapshot.template_digests:
        response = await _run_attested_call(
            ready, git_checkout, step_name, ready.credential["execution_id"], ""
        )
        _assert_digest_recovery(
            response,
            step_name,
            ready.receipt["initialization_id"],
            "recipe_execution_attestation_missing",
        )


@pytest.mark.parametrize("tool_ctx_ready_recipe", _RECIPE_FIXTURE_PARAMETERS, indirect=True)
async def test_wrong_digest_rejection_names_step_and_call(
    tool_ctx_ready_recipe, git_checkout: Path
) -> None:
    ready = tool_ctx_ready_recipe
    installed = get_recipe_execution(ready.tool_ctx)
    assert installed is not None
    wrong_digest = "sha256:" + "0" * 64

    for step_name in installed.snapshot.template_digests:
        response = await _run_attested_call(
            ready,
            git_checkout,
            step_name,
            ready.credential["execution_id"],
            wrong_digest,
        )
        _assert_digest_recovery(
            response,
            step_name,
            ready.receipt["initialization_id"],
            "invocation_template_digest_mismatch",
        )


@pytest.mark.parametrize("tool_ctx_ready_recipe", _RECIPE_FIXTURE_PARAMETERS, indirect=True)
async def test_following_the_recovery_yields_an_admitted_digest(
    tool_ctx_ready_recipe, git_checkout: Path
) -> None:
    from autoskillit.server.tools.tools_recipe import complete_recipe_initialization

    ready = tool_ctx_ready_recipe
    installed = get_recipe_execution(ready.tool_ctx)
    assert installed is not None

    for step_name in installed.snapshot.template_digests:
        missing = await _run_attested_call(
            ready, git_checkout, step_name, ready.credential["execution_id"], ""
        )
        recovery_id = _assert_digest_recovery(
            missing,
            step_name,
            ready.receipt["initialization_id"],
            "recipe_execution_attestation_missing",
        )
        receipt = json.loads(await complete_recipe_initialization(initialization_id=recovery_id))
        assert receipt["success"] is True, receipt
        credential = receipt["recipe_execution"]
        current_step = ready.tool_ctx.active_recipe_steps[step_name]
        with_value = current_step.with_args
        planner_dispatch = (
            ready.tool_ctx.recipe_initialization_state.recipe_name == "planner"
            and isinstance(with_value, dict)
            and "dispatch_items" in with_value
        )
        if planner_dispatch:
            payload = build_run_skill_payload(
                step_name,
                {"with": with_value},
                credential,
                cwd=str(git_checkout),
            )
        else:
            payload = literal_run_skill_payload(
                step_name,
                {"with": ready.tool_ctx.active_recipe_steps[step_name].with_args},
                credential,
                cwd=str(git_checkout),
            )
            payload["skill_command"] = _skill_command(ready.tool_ctx, step_name)
        assert payload["step_name"] == step_name
        expected_template = installed.snapshot.templates[step_name]
        if planner_dispatch:
            skill_inputs = payload.get("skill_inputs")
        else:
            invocation = expected_template.invocation
            contract = get_skill_contract(invocation.skill_name, load_bundled_manifest())
            assert contract is not None
            input_defs = {input_def.name: input_def for input_def in contract.inputs}
            skill_inputs = {}
            for value in invocation.skill_inputs:
                if not value.is_present:
                    continue
                actual = value.effective_value
                if not input_defs[value.name].accepts(actual):
                    assert value.context_dependencies or value.input_dependencies
                    actual = next(
                        sample
                        for sample in ("fixture", 1, False)
                        if input_defs[value.name].accepts(sample)
                    )
                skill_inputs[value.name] = actual
        bound_inputs, admitted_template = bind_attested_runtime_invocation(
            installed,
            execution_id=credential["execution_id"],
            step_name=step_name,
            template_digest=credential["invocation_template_digests"][step_name],
            skill_command=payload["skill_command"],
            skill_inputs=skill_inputs,
            actual_mcp_kwargs=run_skill_actual_mcp_kwargs(payload),
        )
        assert admitted_template is expected_template
        if planner_dispatch:
            item = _SINGLE_BRACE_TOKEN.search(with_value["skill_command"])
            assert item is not None
            assert (
                ORCHESTRATOR_SAMPLES["item"][item.group(1).strip()] in dict(bound_inputs).values()
            )
        else:
            assert dict(bound_inputs) == skill_inputs


@pytest.mark.parametrize("tool_ctx_ready_recipe", _RECIPE_FIXTURE_PARAMETERS, indirect=True)
async def test_wrong_execution_id_rejection_names_the_call(
    tool_ctx_ready_recipe, git_checkout: Path
) -> None:
    ready = tool_ctx_ready_recipe
    installed = get_recipe_execution(ready.tool_ctx)
    assert installed is not None
    actual_id = ready.credential["execution_id"]
    wrong_id = f"{int(actual_id[0], 16) ^ 1:x}" + actual_id[1:]

    for step_name in installed.snapshot.template_digests:
        response = await _run_attested_call(
            ready,
            git_checkout,
            step_name,
            wrong_id,
            "sha256:" + "0" * 64,
        )
        _assert_digest_recovery(
            response,
            step_name,
            ready.receipt["initialization_id"],
            "recipe_execution_id_mismatch",
        )


@pytest.mark.parametrize("tool_ctx_ready_recipe", IMPLEMENTATION_READY_PARAMETERS, indirect=True)
async def test_attested_review_binds_producer_text_pr_number(tool_ctx_ready_recipe) -> None:
    ready = tool_ctx_ready_recipe
    installed = get_recipe_execution(ready.tool_ctx)
    assert installed is not None

    step_name = "review_pr"
    template = installed.snapshot.templates[step_name]
    invocation = template.invocation
    assert invocation.skill_name is not None
    contract = get_skill_contract(invocation.skill_name, load_bundled_manifest())
    assert contract is not None
    input_defs = {input_def.name: input_def for input_def in contract.inputs}
    skill_inputs = {}
    for value in invocation.skill_inputs:
        if not value.is_present:
            continue
        if value.name == "pr_number":
            assert value.is_dynamic
            actual = "31"
        else:
            actual = value.effective_value
            if not input_defs[value.name].accepts(actual):
                assert value.is_dynamic
                actual = next(
                    sample
                    for sample in ("fixture", 1, False)
                    if input_defs[value.name].accepts(sample)
                )
        skill_inputs[value.name] = actual

    credential = ready.credential
    actual_mcp_kwargs = {
        **{value.name: value.effective_value for value in invocation.mcp_kwargs},
        "step_name": step_name,
        "recipe_execution_id": credential["execution_id"],
        "invocation_template_digest": credential["invocation_template_digests"][step_name],
    }
    skill_command = _skill_command(ready.tool_ctx, step_name)
    bound_from_text, admitted_template = bind_attested_runtime_invocation(
        installed,
        execution_id=credential["execution_id"],
        step_name=step_name,
        template_digest=credential["invocation_template_digests"][step_name],
        skill_command=skill_command,
        skill_inputs=skill_inputs,
        actual_mcp_kwargs=actual_mcp_kwargs,
    )
    assert admitted_template is template
    assert ("pr_number", 31) in bound_from_text

    native_skill_inputs = {**skill_inputs, "pr_number": 31}
    bound_from_native, _ = bind_attested_runtime_invocation(
        installed,
        execution_id=credential["execution_id"],
        step_name=step_name,
        template_digest=credential["invocation_template_digests"][step_name],
        skill_command=skill_command,
        skill_inputs=native_skill_inputs,
        actual_mcp_kwargs=actual_mcp_kwargs,
    )
    assert compute_runtime_binding_digest(
        execution_id=credential["execution_id"],
        step_name=step_name,
        template_digest=template.template_digest,
        bound_inputs=bound_from_text,
        actual_mcp_kwargs=actual_mcp_kwargs,
        preflight=None,
    ) == compute_runtime_binding_digest(
        execution_id=credential["execution_id"],
        step_name=step_name,
        template_digest=template.template_digest,
        bound_inputs=bound_from_native,
        actual_mcp_kwargs=actual_mcp_kwargs,
        preflight=None,
    )

    prompt = build_bound_child_prompt(skill_command, bound_from_text, preflight=None)
    marker = "AUTOSKILLIT_BOUND_INVOCATION_V1\n"
    assert marker in prompt
    payload = json.loads(prompt.split(marker, 1)[1])
    assert {"name": "pr_number", "value": 31} in payload["skill_inputs"]
