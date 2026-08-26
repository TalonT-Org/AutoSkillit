"""Per-family focused tests for content-structure SKILL.md semantic rules.

Covers the single `@semantic_rule` check that lives in
`rules_skill_content_content_structure.py`:

  - transition-boundary-anti-confirmation

These tests were relocated verbatim from `tests/recipe/test_rules_skill_content.py`
as part of the #4852 decomposition; no test bodies were edited.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

import autoskillit.recipe._skill_helpers as _sh
from autoskillit.core import Severity
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from tests.recipe.rules_skills._helpers import make_recipe_for_skill

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


# ---------------------------------------------------------------------------
# transition-boundary-anti-confirmation tests
# ---------------------------------------------------------------------------

_BOUNDARY_RULE_ID = "transition-boundary-anti-confirmation"

_SYNTHETIC_BOUNDARY_SKILL_FIRES = textwrap.dedent(
    """\
    # test-skill

    ## EXECUTION MAP

    Group iteration is outer loop. The group number (1, 2, 3, ...) is the
    primary ordering within the dispatch plan.
    Merge-wait is mandatory between groups. After all Group N pipelines
    complete, verify all Group N PRs have merged before starting Group N+1.
    """
)

_SYNTHETIC_BOUNDARY_SKILL_PASSES = textwrap.dedent(
    """\
    # test-skill

    ## EXECUTION MAP

    Group iteration is outer loop. The group number (1, 2, 3, ...) is the
    primary ordering within the dispatch plan.
    Merge-wait is mandatory between groups. After all Group N pipelines
    complete, verify all Group N PRs have merged before starting Group N+1.
    NEVER use AskUserQuestion to ask whether to proceed to the next group.
    """
)


def test_transition_boundary_anti_confirmation_rule_fires(tmp_path: Path) -> None:
    """Rule must emit a finding when a SKILL.md section describes a transition boundary
    but contains no anti-confirmation instruction."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_SYNTHETIC_BOUNDARY_SKILL_FIRES)

    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(make_recipe_for_skill("test-skill", {}))
    recipe = load_recipe(recipe_path)

    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)

    rule_ids = [f.rule for f in findings]
    assert _BOUNDARY_RULE_ID in rule_ids, (
        f"Expected '{_BOUNDARY_RULE_ID}' finding for skill with unprotected group boundary, "
        f"got: {rule_ids}"
    )
    matching = [f for f in findings if f.rule == _BOUNDARY_RULE_ID]
    assert matching[0].severity == Severity.WARNING


def test_transition_boundary_anti_confirmation_rule_passes(tmp_path: Path) -> None:
    """Rule must NOT emit a finding when the transition boundary has an anti-confirmation
    instruction in the same section."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_SYNTHETIC_BOUNDARY_SKILL_PASSES)

    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(make_recipe_for_skill("test-skill", {}))
    recipe = load_recipe(recipe_path)

    with patch.object(_sh, "SKILL_SEARCH_DIRS", [tmp_path]):
        findings = run_semantic_rules(recipe)

    rule_ids = [f.rule for f in findings]
    assert _BOUNDARY_RULE_ID not in rule_ids, (
        f"Rule must not fire when anti-confirmation instruction is present, got: {rule_ids}"
    )
