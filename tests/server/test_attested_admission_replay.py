"""Replay every delivered built-in run_skill step through admission."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from autoskillit.core import (
    InstalledRecipeExecution,
    RecipeSource,
    build_recipe_execution_credential,
)
from autoskillit.core.io import load_yaml
from autoskillit.recipe import load_and_validate
from autoskillit.recipe.schema import RecipeKind
from autoskillit.server.recipe._recipe_execution import (
    RecipeExecutionAdmissionError,
    bind_attested_runtime_invocation,
    build_recipe_execution_snapshot,
)
from tests._run_skill_caller_oracle import (
    AdmissionDefect,
    build_run_skill_payload,
    run_skill_actual_mcp_kwargs,
)
from tests._tracked_recipes import tracked_recipe_load_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[2]
_PINNED: dict[tuple[str, str], Any] = {
    ("planner", "elaborate_phases"): pytest.param(
        "planner",
        "elaborate_phases",
        "recipe_execution_static_input_mismatch",
        "static skill input 'target_phase_id'",
        id="planner-elaborate_phases",
        marks=pytest.mark.xfail(
            strict=True,
            raises=AdmissionDefect,
            reason="Blocked by #5307: planner item placeholders compile as static inputs",
        ),
    ),
    ("planner", "elaborate_assignments"): pytest.param(
        "planner",
        "elaborate_assignments",
        "recipe_execution_static_input_mismatch",
        "static skill input 'context_file'",
        id="planner-elaborate_assignments",
        marks=pytest.mark.xfail(
            strict=True,
            raises=AdmissionDefect,
            reason="Blocked by #5307: planner item placeholders compile as static inputs",
        ),
    ),
    ("planner", "refine_assignments"): pytest.param(
        "planner",
        "refine_assignments",
        "recipe_execution_static_input_mismatch",
        "static skill input 'context_file'",
        id="planner-refine_assignments",
        marks=pytest.mark.xfail(
            strict=True,
            raises=AdmissionDefect,
            reason="Blocked by #5307: planner item placeholders compile as static inputs",
        ),
    ),
    ("planner", "elaborate_wps"): pytest.param(
        "planner",
        "elaborate_wps",
        "recipe_execution_static_input_mismatch",
        "static skill input 'context_file'",
        id="planner-elaborate_wps",
        marks=pytest.mark.xfail(
            strict=True,
            raises=AdmissionDefect,
            reason="Blocked by #5307: planner item placeholders compile as static inputs",
        ),
    ),
    ("planner", "refine_wps"): pytest.param(
        "planner",
        "refine_wps",
        "recipe_execution_static_input_mismatch",
        "static skill input 'context_file'",
        id="planner-refine_wps",
        marks=pytest.mark.xfail(
            strict=True,
            raises=AdmissionDefect,
            reason="Blocked by #5307: planner item placeholders compile as static inputs",
        ),
    ),
    ("merge-prs", "merge_pr"): pytest.param(
        "merge-prs",
        "merge_pr",
        "recipe_execution_static_input_mismatch",
        "static skill input 'pr_number'",
        id="merge-prs-merge_pr",
        marks=pytest.mark.xfail(
            strict=True,
            raises=AdmissionDefect,
            reason="Blocked by #5306: merge item placeholders compile as static inputs",
        ),
    ),
    ("research-implement", "decompose_phases"): pytest.param(
        "research-implement",
        "decompose_phases",
        "recipe_execution_step_mismatch",
        "step is not present in the active compiled execution",
        id="research-implement-decompose_phases",
        marks=pytest.mark.xfail(
            strict=True,
            raises=AdmissionDefect,
            reason="Blocked by #5305: delivered step_name differs from the recipe step key",
        ),
    ),
    ("research-implement", "adjust_experiment"): pytest.param(
        "research-implement",
        "adjust_experiment",
        "recipe_execution_step_mismatch",
        "step is not present in the active compiled execution",
        id="research-implement-adjust_experiment",
        marks=pytest.mark.xfail(
            strict=True,
            raises=AdmissionDefect,
            reason="Blocked by #5305: delivered step_name differs from the recipe step key",
        ),
    ),
    ("research-implement", "fix_tests"): pytest.param(
        "research-implement",
        "fix_tests",
        "recipe_execution_step_mismatch",
        "step is not present in the active compiled execution",
        id="research-implement-fix_tests",
        marks=pytest.mark.xfail(
            strict=True,
            raises=AdmissionDefect,
            reason="Blocked by #5305: delivered step_name differs from the recipe step key",
        ),
    ),
    ("bem-wrapper", "run_bem"): pytest.param(
        "bem-wrapper",
        "run_bem",
        "step_not_installed",
        "missing_tool_parameter: cwd",
        id="bem-wrapper-run_bem",
        marks=pytest.mark.xfail(
            strict=True,
            raises=AdmissionDefect,
            reason="Blocked by #5310: missing required cwd silently prevents step installation",
        ),
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


def _dynamic_step_outcome(steps: dict[str, Any], step_name: str) -> None:
    skill_command = steps[step_name]["with"]["skill_command"]
    first_tokens = skill_command.split()
    assert first_tokens and "{" in first_tokens[0], (
        f"{step_name}: dynamic skill command must template its first token"
    )


def _uninstalled_step_outcome(projection: Any, step_name: str) -> tuple[str, str]:
    failures = projection.invocations[step_name].failures
    detail = "; ".join(f"{failure.code.value}: {failure.name}" for failure in failures)
    return "step_not_installed", detail


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
    snapshot = installed.snapshot
    if step_name in snapshot.dynamic_skill_step_names:
        _dynamic_step_outcome(steps, step_name)
        return None
    if step_name not in snapshot.templates:
        projection = loaded["_finalized_projection"].binding_projection
        return _uninstalled_step_outcome(projection, step_name)
    return _templated_step_outcome(
        step_name,
        steps[step_name],
        installed,
        credential,
        str(tmp_path),
    )


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
