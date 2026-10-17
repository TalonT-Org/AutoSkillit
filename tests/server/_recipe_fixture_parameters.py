"""Shared READY-recipe inputs and parametrization for server tests."""

from pathlib import Path

import pytest

from autoskillit.core import FinalizedRecipeProjection

IMPLEMENTATION_OVERRIDES = {
    "issue_url": "https://github.com/TalonT-Org/AutoSkillit/issues/5256",
    "task": "test task",
}
REVIEW_APPROACH_OVERRIDES = {**IMPLEMENTATION_OVERRIDES, "review_approach": "true"}
IMPLEMENTATION_READY_PARAMETERS = [
    pytest.param(("implementation", "plan", IMPLEMENTATION_OVERRIDES), id="implementation"),
]


def _remediation_ready_parameter() -> tuple[str, str, dict[str, str]]:
    from autoskillit.recipe import load_and_validate

    project_root = Path(__file__).resolve().parents[2]
    loaded = load_and_validate(
        "remediation",
        project_dir=project_root,
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
        raise AssertionError("remediation has no attested run_skill invocation")
    return ("remediation", attested_step, IMPLEMENTATION_OVERRIDES)


REMEDIATION_READY_PARAMETERS = [pytest.param(_remediation_ready_parameter(), id="remediation")]
IMPLEMENTATION_REVIEW_READY_PARAMETERS = [
    pytest.param(("implementation", "plan", IMPLEMENTATION_OVERRIDES), id="default"),
    pytest.param(("implementation", "plan", REVIEW_APPROACH_OVERRIDES), id="review-approach"),
]

RESEARCH_OVERRIDES = {
    "issue_url": "https://github.com/TalonT-Org/AutoSkillit/issues/4411",
    "source_dir": ".",
    "task": "test task",
    "task_description": "test task",
}

RESEARCH_READY_DEFAULT = ("research", "scope", RESEARCH_OVERRIDES)
