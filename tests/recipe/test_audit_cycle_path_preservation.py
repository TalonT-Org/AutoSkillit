"""A rejected audit attempt preserves the last trusted authority path."""

from __future__ import annotations

import pytest

from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.smoke_utils import merge_audit_cycle_path

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


@pytest.mark.parametrize(
    "name",
    (
        "implementation",
        "implementation-groups",
        "remediation",
        "merge-prs",
        "research",
        "research-implement",
    ),
)
def test_only_published_authority_replaces_prior_path(name: str) -> None:
    recipe = load_recipe(builtin_recipes_dir() / f"{name}.yaml")
    audit = recipe.steps["audit_impl"]
    assert audit.capture["audit_cycle_path_raw"].from_ == "${{ result.audit_cycle_path }}"
    assert "audit_cycle_path" not in audit.capture
    assert audit.with_args["skill_inputs"]["prior_audit_cycle_path"] == (
        "${{ context.audit_cycle_path }}"
    )
    writers = [step for step in recipe.steps.values() if "audit_cycle_path" in step.capture]
    assert len(writers) == 1
    assert writers[0].name == "merge_audit_cycle_path"

    assert merge_audit_cycle_path("", "/trusted/prior.json") == {
        "audit_cycle_path": "/trusted/prior.json"
    }
