"""Every invocation-binding failure reaches its declaration-scoped error rule."""

from __future__ import annotations

import pytest

from autoskillit.core import BindingFailureCode, Severity
from autoskillit.recipe import make_validation_context
from autoskillit.recipe._binding import bind_recipe
from autoskillit.recipe.registry import (
    _RULE_REGISTRY,
    declaration_scoped_rule_names,
    run_semantic_rules,
)
from autoskillit.recipe.schema import Recipe, RecipeStep
from tests.recipe._binding_helpers import _manifest, _required_inputs

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_DRY_WALKTHROUGH = "/autoskillit:dry-walkthrough"
_REQUIRED_INPUTS = _required_inputs()

_TRIGGERS: dict[BindingFailureCode, tuple[str, dict[str, object]]] = {
    BindingFailureCode.UNKNOWN_TOOL: ("no_such_tool", {"x": 1}),
    BindingFailureCode.UNKNOWN_TOOL_PARAMETER: (
        "run_cmd",
        {"cmd": "pwd", "cwd": "/r", "bogus": 1},
    ),
    BindingFailureCode.MISSING_TOOL_PARAMETER: ("run_cmd", {"cmd": "pwd"}),
    BindingFailureCode.INVALID_TOOL_PARAMETER_TYPE: (
        "bulk_close_issues",
        {
            "issue_numbers": "${{ context.issue_numbers }}",
            "comment": "c",
            "cwd": "/r",
        },
    ),
    BindingFailureCode.UNKNOWN_SKILL: (
        "run_skill",
        {"skill_command": "/autoskillit:no-such-skill", "cwd": "/r"},
    ),
    BindingFailureCode.UNKNOWN_SKILL_INPUT: (
        "run_skill",
        {
            "skill_command": _DRY_WALKTHROUGH,
            "cwd": "/r",
            "skill_inputs": {**_REQUIRED_INPUTS, "unknown_child": "v"},
        },
    ),
    BindingFailureCode.MISSING_SKILL_INPUT: (
        "run_skill",
        {
            "skill_command": _DRY_WALKTHROUGH,
            "cwd": "/r",
            "skill_inputs": {
                name: value for name, value in _REQUIRED_INPUTS.items() if name != "plan_path"
            },
        },
    ),
    BindingFailureCode.AMBIGUOUS_SKILL_INPUT: (
        "run_skill",
        {
            "skill_command": f"{_DRY_WALKTHROUGH} /tmp/x.md",
            "cwd": "/r",
            "skill_inputs": dict(_REQUIRED_INPUTS),
        },
    ),
    BindingFailureCode.DEAD_SKILL_INPUT: (
        "run_skill",
        {
            "skill_command": (
                f"{_DRY_WALKTHROUGH} /tmp/a https://x /extra /e2 /e3 /e4 /e5 /e6 /e7 /e8"
            ),
            "cwd": "/r",
        },
    ),
    BindingFailureCode.INVALID_SKILL_INPUT_TYPE: (
        "run_skill",
        {
            "skill_command": _DRY_WALKTHROUGH,
            "cwd": "/r",
            "skill_inputs": {**_REQUIRED_INPUTS, "round": "abc"},
        },
    ),
    BindingFailureCode.INVALID_SKILL_COMMAND: (
        "run_skill",
        {
            "skill_command": f"{_DRY_WALKTHROUGH} /tmp/plan.md https://x",
            "cwd": "/r",
        },
    ),
}

_EXPECTED_RULE: dict[BindingFailureCode, str] = {
    BindingFailureCode.UNKNOWN_TOOL: "unknown-tool",
    BindingFailureCode.UNKNOWN_TOOL_PARAMETER: "dead-with-param",
    BindingFailureCode.MISSING_TOOL_PARAMETER: "invalid-tool-invocation-binding",
    BindingFailureCode.INVALID_TOOL_PARAMETER_TYPE: "invalid-tool-invocation-binding",
    BindingFailureCode.MISSING_SKILL_INPUT: "missing-ingredient",
    BindingFailureCode.UNKNOWN_SKILL: "invalid-skill-invocation-binding",
    BindingFailureCode.UNKNOWN_SKILL_INPUT: "invalid-skill-invocation-binding",
    BindingFailureCode.AMBIGUOUS_SKILL_INPUT: "invalid-skill-invocation-binding",
    BindingFailureCode.DEAD_SKILL_INPUT: "invalid-skill-invocation-binding",
    BindingFailureCode.INVALID_SKILL_INPUT_TYPE: "invalid-skill-invocation-binding",
    BindingFailureCode.INVALID_SKILL_COMMAND: "invalid-skill-invocation-binding",
}

_EXPECTED_MESSAGE = {
    BindingFailureCode.UNKNOWN_TOOL: "no_such_tool",
    BindingFailureCode.UNKNOWN_TOOL_PARAMETER: "bogus",
    BindingFailureCode.MISSING_TOOL_PARAMETER: "cwd",
    BindingFailureCode.INVALID_TOOL_PARAMETER_TYPE: "issue_numbers",
    BindingFailureCode.UNKNOWN_SKILL: "selected skill has no canonical contract",
    BindingFailureCode.UNKNOWN_SKILL_INPUT: "unknown_child",
    BindingFailureCode.MISSING_SKILL_INPUT: "plan_path",
    BindingFailureCode.AMBIGUOUS_SKILL_INPUT: "may not mix inline arguments",
    BindingFailureCode.DEAD_SKILL_INPUT: "more positional values than the contract",
    BindingFailureCode.INVALID_SKILL_INPUT_TYPE: "round",
    BindingFailureCode.INVALID_SKILL_COMMAND: "effective skill arguments do not align",
}


def _recipe_for(code: BindingFailureCode) -> Recipe:
    tool, with_args = _TRIGGERS[code]
    step = RecipeStep(name="s", tool=tool, with_args=dict(with_args))
    if code is BindingFailureCode.INVALID_SKILL_COMMAND:
        step.declared_with_args = {
            "skill_command": f"{_DRY_WALKTHROUGH} /tmp/plan.md",
            "cwd": "/r",
        }
    return Recipe(name="r", description="d", steps={"s": step})


def test_tables_cover_every_code() -> None:
    assert len(BindingFailureCode.__members__) == len(BindingFailureCode)
    assert (
        set(_TRIGGERS) == set(_EXPECTED_RULE) == set(_EXPECTED_MESSAGE) == set(BindingFailureCode)
    )


@pytest.mark.parametrize("code", list(_TRIGGERS), ids=lambda code: code.name.lower())
def test_code_reaches_expected_rule(code: BindingFailureCode) -> None:
    recipe = _recipe_for(code)
    projection = bind_recipe(recipe, manifest=_manifest())

    assert code in {failure.code for failure in projection.for_step("s").failures}

    context = make_validation_context(recipe, binding_projection=projection)
    findings = run_semantic_rules(context)
    finding = next(
        (
            candidate
            for candidate in findings
            if candidate.rule == _EXPECTED_RULE[code]
            and candidate.step_name == "s"
            and candidate.severity is Severity.ERROR
            and _EXPECTED_MESSAGE[code] in candidate.message
        ),
        None,
    )
    assert finding is not None


def test_complete_tool_invocation_has_no_binding_error() -> None:
    recipe = Recipe(
        name="r",
        description="d",
        steps={
            "s": RecipeStep(
                name="s",
                tool="commit_files",
                with_args={"paths": ["plan.md"], "message": "Save plan", "cwd": "/r"},
            )
        },
    )
    projection = bind_recipe(recipe, manifest=_manifest())
    assert projection.for_step("s").is_valid

    findings = run_semantic_rules(make_validation_context(recipe, binding_projection=projection))
    assert not any(finding.rule == "invalid-tool-invocation-binding" for finding in findings)


def test_binding_reporters_are_error_and_declaration_scoped() -> None:
    expected_rules = set(_EXPECTED_RULE.values()) | {
        "step-identity-mismatch",
        "unresolved-skill-input-template",
    }
    rules = {rule.name: rule for rule in _RULE_REGISTRY}
    assert declaration_scoped_rule_names() == expected_rules

    for name in expected_rules:
        rule = rules[name]
        assert rule.severity is Severity.ERROR
        assert rule.declaration_scoped is True
