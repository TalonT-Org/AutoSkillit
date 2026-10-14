"""Shared READY-recipe inputs and parametrization for server tests."""

import pytest

IMPLEMENTATION_OVERRIDES = {
    "issue_url": "https://github.com/TalonT-Org/AutoSkillit/issues/5256",
    "task": "test task",
}
REVIEW_APPROACH_OVERRIDES = {**IMPLEMENTATION_OVERRIDES, "review_approach": "true"}
IMPLEMENTATION_READY_PARAMETERS = [
    pytest.param(("implementation", "plan", IMPLEMENTATION_OVERRIDES), id="implementation"),
]

RESEARCH_OVERRIDES = {
    "issue_url": "https://github.com/TalonT-Org/AutoSkillit/issues/4411",
    "source_dir": ".",
    "task": "test task",
    "task_description": "test task",
}
