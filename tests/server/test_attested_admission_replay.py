"""Replay statically resolved built-in run_skill steps through admission.

Dynamic skill commands require runtime resolution and are explicitly skipped.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn, cast

import pytest

from autoskillit.core import (
    BoundValueOrigin,
    InstalledRecipeExecution,
    RecipeSource,
    build_recipe_execution_credential,
)
from autoskillit.core.io import load_yaml
from autoskillit.recipe import load_and_validate
from autoskillit.recipe.schema import RecipeKind
from autoskillit.server.recipe._admission import RecipeExecutionAdmissionError
from autoskillit.server.recipe._recipe_execution import (
    bind_attested_runtime_invocation,
    build_recipe_execution_snapshot,
)
from tests._run_skill_caller_oracle import (
    _SINGLE_BRACE_TOKEN,
    ORCHESTRATOR_SAMPLES,
    AdmissionDefect,
    build_run_skill_payload,
    run_skill_actual_mcp_kwargs,
)
from tests._tracked_recipes import tracked_recipe_load_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[2]
_PINNED: dict[tuple[str, str], Any] = {
    ("bem-wrapper", "run_bem"): pytest.param(
        "bem-wrapper", "run_bem", None, None, id="bem-wrapper-run_bem"
    ),
}


@cache
def _load_recipe(
    recipe_name: str,
) -> tuple[Any, InstalledRecipeExecution, dict[str, Any], dict[str, Any]]:
    loaded = load_and_validate(
        recipe_name,
        project_dir=REPO_ROOT,
        include_finalized_projection=True,
    )
    if not loaded["valid"]:
        raise AssertionError(f"invalid built-in recipe {recipe_name!r}: {loaded['errors']}")

    projection = loaded["_finalized_projection"].binding_projection
    snapshot = build_recipe_execution_snapshot(
        recipe_name=recipe_name,
        content_hash=loaded["content_hash"],
        composite_hash=loaded["composite_hash"],
        projection=projection,
    )
    installed = cast(InstalledRecipeExecution, SimpleNamespace(snapshot=snapshot))
    credential = build_recipe_execution_credential(snapshot).as_wire_block()
    recipe_document = cast(dict[str, Any], load_yaml(loaded["content"]))
    steps = cast(dict[str, Any], recipe_document["steps"])
    return loaded, installed, credential, steps


def _replay_parameters() -> list[Any]:
    parameters: list[Any] = []
    seen_pins: set[tuple[str, str]] = set()
    for info in tracked_recipe_load_result(REPO_ROOT).items:
        if info.source is not RecipeSource.BUILTIN or info.kind is RecipeKind.CAMPAIGN:
            continue
        loaded, _, _, steps = _load_recipe(info.name)
        post_prune_steps = loaded["post_prune_step_names"]
        for step_name, step_body in steps.items():
            if step_name not in post_prune_steps or step_body.get("tool") != "run_skill":
                continue
            key = (info.name, step_name)
            seen_pins.add(key)
            parameters.append(
                _PINNED[key]
                if key in _PINNED
                else pytest.param(info.name, step_name, None, None, id=f"{info.name}-{step_name}")
            )

    parameters.extend(pin for key, pin in _PINNED.items() if key not in seen_pins)
    return parameters


def _dispatch_step_parameters() -> list[Any]:
    dispatch_steps: list[tuple[str, str]] = []
    for info in tracked_recipe_load_result(REPO_ROOT).items:
        if info.source is not RecipeSource.BUILTIN or info.kind is RecipeKind.CAMPAIGN:
            continue
        loaded, _, _, steps = _load_recipe(info.name)
        post_prune_steps = loaded["post_prune_step_names"]
        for step_name, step_body in steps.items():
            with_value = step_body.get("with", {})
            if (
                step_name in post_prune_steps
                and step_body.get("tool") == "run_skill"
                and isinstance(with_value, dict)
                and "dispatch_items" in with_value
            ):
                dispatch_steps.append((info.name, step_name))

    assert dispatch_steps, "no builtin run_skill dispatch steps were found"
    assert ("planner", "elaborate_phases") in dispatch_steps
    return [pytest.param(recipe, step, id=f"{recipe}-{step}") for recipe, step in dispatch_steps]


_DISPATCH_STEP_PARAMETERS = _dispatch_step_parameters()


def _dispatch_item_name(step_body: dict[str, Any]) -> str:
    with_value = step_body.get("with")
    if not isinstance(with_value, dict):
        raise AssertionError("dispatch step must declare a with mapping")
    names = {
        match.group(1).strip()
        for value in with_value.values()
        for match in _SINGLE_BRACE_TOKEN.finditer(repr(value))
    }
    if len(names) != 1:
        raise AssertionError(f"expected one dispatch item placeholder, found {sorted(names)}")
    return next(iter(names))


def _dynamic_step_outcome(step_name: str) -> NoReturn:
    pytest.skip(
        f"{step_name}: dynamic skill command requires runtime resolution before admission replay"
    )


def _templated_step_outcome(
    step_name: str,
    step_body: dict[str, Any],
    installed: InstalledRecipeExecution,
    credential: dict[str, Any],
    cwd: str,
) -> tuple[str, str] | None:
    payload = build_run_skill_payload(
        step_name,
        step_body,
        credential,
        cwd=cwd,
    )
    try:
        bind_attested_runtime_invocation(
            installed,
            execution_id=payload["recipe_execution_id"],
            step_name=payload["step_name"],
            template_digest=payload["invocation_template_digest"],
            skill_command=payload["skill_command"],
            skill_inputs=payload.get("skill_inputs"),
            actual_mcp_kwargs=run_skill_actual_mcp_kwargs(payload),
        )
    except RecipeExecutionAdmissionError as exc:
        return exc.code, str(exc)
    return None


def _step_outcome(
    recipe_name: str,
    step_name: str,
    tmp_path: Path,
) -> tuple[str, str] | None:
    loaded, installed, credential, steps = _load_recipe(recipe_name)
    if (
        step_name not in loaded["post_prune_step_names"]
        or steps.get(step_name, {}).get("tool") != "run_skill"
    ):
        raise AssertionError(
            f"stale admission replay pin {recipe_name}.{step_name}: "
            "no delivered run_skill step matches this pin"
        )
    snapshot = installed.snapshot
    if step_name in snapshot.dynamic_skill_step_names:
        _dynamic_step_outcome(step_name)
    return _templated_step_outcome(
        step_name,
        steps[step_name],
        installed,
        credential,
        str(tmp_path),
    )


def test_dynamic_steps_are_explicitly_skipped() -> None:
    with pytest.raises(pytest.skip.Exception, match="dynamic.*runtime resolution"):
        _dynamic_step_outcome("dispatch")


def _assert_admitted(
    outcome: tuple[str, str] | None,
    pin_code: str | None,
    pin_detail: str | None,
    diagnostic: str,
) -> None:
    if outcome is None:
        return
    code, detail = outcome
    if (
        pin_code is not None
        and pin_detail is not None
        and code == pin_code
        and pin_detail in detail
    ):
        raise AdmissionDefect(f"{diagnostic}: {code}: {detail}")
    raise AssertionError(f"{diagnostic}: unexpected admission rejection {code!r}: {detail}")


@pytest.mark.parametrize(
    ("outcome", "pin_code", "pin_detail", "expected_type"),
    [
        pytest.param(None, None, None, None, id="admitted"),
        pytest.param(
            ("rejected", "expected detail is present"),
            "rejected",
            "expected detail",
            AdmissionDefect,
            id="exact-pin",
        ),
        pytest.param(
            ("different", "expected detail is present"),
            "rejected",
            "expected detail",
            AssertionError,
            id="different-code",
        ),
        pytest.param(
            ("rejected", "other detail"),
            "rejected",
            "expected detail",
            AssertionError,
            id="different-detail",
        ),
    ],
)
def test_assert_admitted_only_accepts_the_exact_pin(
    outcome: tuple[str, str] | None,
    pin_code: str | None,
    pin_detail: str | None,
    expected_type: type[BaseException] | None,
) -> None:
    if expected_type is None:
        assert _assert_admitted(outcome, pin_code, pin_detail, "recipe.step") is None
        return
    with pytest.raises(expected_type) as exc_info:
        _assert_admitted(outcome, pin_code, pin_detail, "recipe.step")
    assert type(exc_info.value) is expected_type


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("run {phase_id}", "run P1"),
        ("run {{AUTOSKILLIT_TEMP}}", "run {{AUTOSKILLIT_TEMP}}"),
        ("run ${{ env.NAME }}", "run ${{ env.NAME }}"),
    ],
)
def test_payload_substitutes_only_supported_tokens(command: str, expected: str) -> None:
    payload = build_run_skill_payload(
        "P1",
        {"with": {"skill_command": command}},
        {"execution_id": "exec", "invocation_template_digests": {"P1": "digest"}},
        cwd="/repo",
    )
    assert payload["skill_command"] == expected


@pytest.mark.parametrize("missing_required", [False, True])
def test_payload_uses_credential_input_order_and_requires_caller_values(
    missing_required: bool,
) -> None:
    step = {
        "with": {
            "cwd": "${{ context.repo }}",
            "skill_inputs": {} if missing_required else {"required": "${{ context.pr_number }}"},
        }
    }
    credential = {
        "execution_id": "exec",
        "invocation_template_digests": {"verify": "digest"},
        "skill_input_shapes": {
            "verify": {"keys": ["required", "optional"], "absence_values": {"optional": ""}}
        },
    }
    if missing_required:
        with pytest.raises(AssertionError, match="verify.*required.*no caller value"):
            build_run_skill_payload("verify", step, credential, cwd="/repo")
        return
    payload = build_run_skill_payload("verify", step, credential, cwd="/repo")
    assert payload["cwd"] == "/repo"
    skill_inputs = payload["skill_inputs"]
    assert isinstance(skill_inputs, dict)
    assert list(skill_inputs.items()) == [("required", 7), ("optional", "")]


@pytest.mark.parametrize(("recipe", "step"), _DISPATCH_STEP_PARAMETERS)
def test_dispatch_steps_bind_the_orchestrator_item(
    recipe: str,
    step: str,
    tmp_path: Path,
) -> None:
    _, installed, credential, steps = _load_recipe(recipe)
    payload = build_run_skill_payload(step, steps[step], credential, cwd=str(tmp_path))

    bound_inputs, admitted_template = bind_attested_runtime_invocation(
        installed,
        execution_id=payload["recipe_execution_id"],
        step_name=payload["step_name"],
        template_digest=payload["invocation_template_digest"],
        skill_command=payload["skill_command"],
        skill_inputs=payload.get("skill_inputs"),
        actual_mcp_kwargs=run_skill_actual_mcp_kwargs(payload),
    )

    assert admitted_template is installed.snapshot.templates[step]
    item_name = _dispatch_item_name(steps[step])
    assert ORCHESTRATOR_SAMPLES["item"][item_name] in dict(bound_inputs).values()


@pytest.mark.parametrize(("recipe", "step"), _DISPATCH_STEP_PARAMETERS)
def test_unsubstituted_dispatch_item_is_rejected(
    recipe: str,
    step: str,
    tmp_path: Path,
) -> None:
    _, installed, credential, steps = _load_recipe(recipe)
    payload = build_run_skill_payload(step, steps[step], credential, cwd=str(tmp_path))
    item_name = _dispatch_item_name(steps[step])
    sample = str(ORCHESTRATOR_SAMPLES["item"][item_name])
    command = payload.get("skill_command")
    assert isinstance(command, str)
    sample_token = re.compile(rf"(?<!\S){re.escape(sample)}(?!\S)")
    assert len(list(sample_token.finditer(command))) == 1
    payload["skill_command"] = sample_token.sub("{" + item_name + "}", command, count=1)

    with pytest.raises(RecipeExecutionAdmissionError) as exc_info:
        bind_attested_runtime_invocation(
            installed,
            execution_id=payload["recipe_execution_id"],
            step_name=payload["step_name"],
            template_digest=payload["invocation_template_digest"],
            skill_command=payload["skill_command"],
            skill_inputs=payload.get("skill_inputs"),
            actual_mcp_kwargs=run_skill_actual_mcp_kwargs(payload),
        )

    assert exc_info.value.code == "recipe_execution_input_type"
    dispatch_input = next(
        value
        for value in installed.snapshot.templates[step].invocation.skill_inputs
        if value.origin is BoundValueOrigin.DISPATCH_ITEM
    )
    assert dispatch_input.name in str(exc_info.value)
    assert "unsubstituted" in str(exc_info.value).lower()


@pytest.mark.parametrize(
    ("recipe", "step", "pin_code", "pin_detail"),
    _replay_parameters(),
)
def test_run_skill_step_is_admitted_for_its_orchestrator_payload(
    recipe: str,
    step: str,
    pin_code: str | None,
    pin_detail: str | None,
    tmp_path: Path,
) -> None:
    outcome = _step_outcome(recipe, step, tmp_path)
    _assert_admitted(outcome, pin_code, pin_detail, f"{recipe}.{step}")
