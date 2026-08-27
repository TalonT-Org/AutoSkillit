"""Filter-cascade tests for the #4852 rules_skill_content split.

Verifies that `tests/_test_filter.py::MODULE_CASCADE_RECIPE` carries one
entry per new sibling module stem, and that each entry points at least at
the per-family test file in `tests/recipe/rules_skills/` plus the parent
`recipe` directory.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_new_module_stems_in_module_cascade_recipe() -> None:
    """Four new sibling module stems appear in MODULE_CASCADE_RECIPE."""
    from tests._test_filter import MODULE_CASCADE_RECIPE

    expected_stems = {
        "rules_skill_content_shell_safety",
        "rules_skill_content_github_api_safety",
        "rules_skill_content_content_structure",
        "rules_skill_content_skill_contract",
    }
    actual_stems = set(MODULE_CASCADE_RECIPE.keys())
    missing = expected_stems - actual_stems
    assert not missing, f"Missing cascade entries: {missing}"

    # Each entry must reference at least the parent `recipe` directory and
    # one per-family test file in `recipe/rules_skills/`.
    family_to_test = {
        "rules_skill_content_shell_safety": (
            "recipe/rules_skills/" + "test_split_per_family_focused_shell_safety.py"
        ),
        "rules_skill_content_github_api_safety": (
            "recipe/rules_skills/" + "test_split_per_family_focused_github_api_safety.py"
        ),
        "rules_skill_content_content_structure": (
            "recipe/rules_skills/" + "test_split_per_family_focused_content_structure.py"
        ),
        "rules_skill_content_skill_contract": (
            "recipe/rules_skills/" + "test_split_per_family_focused_skill_contract.py"
        ),
    }
    for stem, expected_test in family_to_test.items():
        targets = MODULE_CASCADE_RECIPE[stem]
        assert "recipe" in targets, f"{stem}: missing 'recipe' in cascade targets"
        assert expected_test in targets, (
            f"{stem}: missing per-family test file {expected_test!r} "
            f"in cascade targets; got {targets}"
        )


def test_facade_stem_still_in_module_cascade_recipe() -> None:
    """The rules_skill_content facade stem must remain in MODULE_CASCADE_RECIPE."""
    from tests._test_filter import MODULE_CASCADE_RECIPE

    assert "rules_skill_content" in MODULE_CASCADE_RECIPE, (
        "rules_skill_content facade stem must remain in MODULE_CASCADE_RECIPE"
    )
    targets = MODULE_CASCADE_RECIPE["rules_skill_content"]
    assert "recipe/test_rules_skill_content.py" in targets, (
        f"rules_skill_content facade must reference the smoke-test file; got {targets}"
    )
