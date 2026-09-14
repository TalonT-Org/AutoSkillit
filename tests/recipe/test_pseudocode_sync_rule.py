"""Tests for the pseudocode-callable-divergence semantic rule."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

import autoskillit.recipe.helpers._skill_helpers as _sh
import autoskillit.recipe.rules.rules_pseudocode_sync as _pseudocode_sync
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_RECIPE_WITH_REVIEW_FAMILY = textwrap.dedent(
    """\
    name: test-recipe
    kitchen_rules:
      - "Use run_skill and run_python."
    steps:
      run_verdict:
        tool: run_python
        phoropter_family: review-design
        with:
          callable: "autoskillit.smoke_utils.aggregate_review_verdict"
        on_success: done
      run_review:
        tool: run_skill
        phoropter_family: review-design
        with:
          skill_command: "/autoskillit:review-design"
        on_success: done
    """
)

_RECIPE_NON_SMOKE_UTILS = textwrap.dedent(
    """\
    name: test-recipe
    kitchen_rules:
      - "Use run_skill and run_python."
    steps:
      run_verdict:
        tool: run_python
        phoropter_family: review-design
        with:
          callable: "some_other_package.utils.do_thing"
        on_success: done
      run_review:
        tool: run_skill
        phoropter_family: review-design
        with:
          skill_command: "/autoskillit:review-design"
        on_success: done
    """
)

# SKILL.md with inlined frozenset members — rule must fire
_SKILL_MD_INLINED = textwrap.dedent(
    """\
    # review-design
    ## Arguments
    None.

    ### Step 7
    ```python
    structural_stop_triggers = [
        f for f in l1_criticals
        if f.fixability == "STRUCTURAL" or f.fixability is None
    ]
    ```

    ### Step 8
    Write output.
    """
)

# SKILL.md with constant referenced by name — rule must NOT fire
_SKILL_MD_BY_NAME = textwrap.dedent(
    """\
    # review-design
    ## Arguments
    None.

    ### Step 7
    ```python
    structural_stop_triggers = [
        f for f in l1_criticals
        if f.fixability in _STRUCTURAL_FIXABILITY_VALUES  # {"STRUCTURAL", None}
    ]
    ```

    ### Step 8
    Write output.
    """
)

# SKILL.md with no python blocks — rule must NOT fire
_SKILL_MD_NO_PYTHON = textwrap.dedent(
    """\
    # review-design
    ## Arguments
    None.

    ### Step 7
    Only prose here, no python blocks.

    ### Step 8
    Write output.
    """
)


def _write_skill(tmp_path: Path, content: str, skill_name: str = "review-design") -> None:
    skill_dir = tmp_path / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content)


def test_find_frozenset_constants_ignores_function_local_constants(tmp_path: Path) -> None:
    """The scanner considers only direct module children."""
    (tmp_path / "constants.py").write_text(
        "TOP_LEVEL = frozenset({'top'})\n\n"
        "def build_values() -> None:\n"
        "    LOCAL = frozenset({'local'})\n"
    )

    constants = _pseudocode_sync._find_frozenset_constants(tmp_path)

    assert constants == {"TOP_LEVEL": frozenset({"top"})}


def test_rule_fires_for_inlined_constant_members(tmp_path: Path) -> None:
    """Rule emits WARNING when SKILL.md inlines frozenset members without naming the constant."""
    _write_skill(tmp_path, _SKILL_MD_INLINED)
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_RECIPE_WITH_REVIEW_FAMILY)
    recipe = load_recipe(recipe_path)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(_sh, "SKILL_SEARCH_DIRS", [tmp_path])
        findings = run_semantic_rules(recipe)

    matching = [f for f in findings if f.rule == "pseudocode-callable-divergence"]
    assert matching, (
        "Expected pseudocode-callable-divergence finding when constant members are inlined"
    )
    assert all(f.severity.name == "WARNING" for f in matching)


def test_rule_orders_findings_by_skill_then_source_constant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Matching skills precede source constants in pseudocode-divergence findings."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "constants.py").write_text(
        "FIRST_VALUES = frozenset({'first'})\nSECOND_VALUES = frozenset({'second'})\n"
    )
    inlined_constants = textwrap.dedent(
        """\
        # test skill
        ```python
        first_value = "first"
        second_value = "second"
        ```
        """
    )
    _write_skill(tmp_path, inlined_constants, skill_name="review-a")
    _write_skill(tmp_path, inlined_constants, skill_name="review-b")
    recipe = Recipe(
        name="test-recipe",
        description="test",
        kitchen_rules=["Use run_skill and run_python."],
        steps={
            "run_verdict": RecipeStep(
                tool="run_python",
                phoropter_family="review-design",
                with_args={"callable": "autoskillit.smoke_utils.aggregate_review_verdict"},
            ),
            "review_a": RecipeStep(
                tool="run_skill",
                phoropter_family="review-design",
                with_args={"skill_command": "/autoskillit:review-a"},
            ),
            "review_b": RecipeStep(
                tool="run_skill",
                phoropter_family="review-design",
                with_args={"skill_command": "/autoskillit:review-b"},
            ),
        },
    )
    monkeypatch.setattr(_sh, "SKILL_SEARCH_DIRS", [tmp_path])
    monkeypatch.setattr(
        _pseudocode_sync,
        "_get_package_source_dir",
        lambda _callable_path: source_dir,
    )

    findings = run_semantic_rules(recipe)

    matching = [f for f in findings if f.rule == "pseudocode-callable-divergence"]
    expected = [
        ("review-a", "FIRST_VALUES"),
        ("review-a", "SECOND_VALUES"),
        ("review-b", "FIRST_VALUES"),
        ("review-b", "SECOND_VALUES"),
    ]
    assert len(matching) == len(expected)
    for finding, (skill_name, constant_name) in zip(matching, expected, strict=True):
        assert f"SKILL.md for '{skill_name}'" in finding.message
        assert f"all members of '{constant_name}'" in finding.message


def test_rule_silent_when_constant_referenced_by_name(tmp_path: Path) -> None:
    """Rule emits no finding when SKILL.md references the constant by name."""
    _write_skill(tmp_path, _SKILL_MD_BY_NAME)
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_RECIPE_WITH_REVIEW_FAMILY)
    recipe = load_recipe(recipe_path)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(_sh, "SKILL_SEARCH_DIRS", [tmp_path])
        findings = run_semantic_rules(recipe)

    matching = [f for f in findings if f.rule == "pseudocode-callable-divergence"]
    assert not matching, (
        "Expected no pseudocode-callable-divergence finding when constant is named; "
        f"got: {matching}"
    )


def test_rule_silent_for_non_smoke_utils_callable(tmp_path: Path) -> None:
    """Rule does not fire for callables outside autoskillit.smoke_utils."""
    _write_skill(tmp_path, _SKILL_MD_INLINED)
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_RECIPE_NON_SMOKE_UTILS)
    recipe = load_recipe(recipe_path)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(_sh, "SKILL_SEARCH_DIRS", [tmp_path])
        findings = run_semantic_rules(recipe)

    matching = [f for f in findings if f.rule == "pseudocode-callable-divergence"]
    assert not matching, "Rule must not fire for callables outside autoskillit.smoke_utils"


def test_rule_silent_when_no_python_blocks(tmp_path: Path) -> None:
    """Rule does not fire for run_skill steps whose SKILL.md has no python pseudocode blocks."""
    _write_skill(tmp_path, _SKILL_MD_NO_PYTHON)
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(_RECIPE_WITH_REVIEW_FAMILY)
    recipe = load_recipe(recipe_path)

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(_sh, "SKILL_SEARCH_DIRS", [tmp_path])
        findings = run_semantic_rules(recipe)

    matching = [f for f in findings if f.rule == "pseudocode-callable-divergence"]
    assert not matching, "Rule must not fire when SKILL.md has no python pseudocode blocks"
