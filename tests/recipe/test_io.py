"""Tests for recipe I/O and parsing (recipe_io module)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core.types import RecipeSource
from autoskillit.recipe.io import (
    _parse_recipe,
    _parse_step,
    builtin_recipes_dir,
    list_recipes,
    load_recipe,
)
from autoskillit.recipe.schema import (
    Recipe,
    RecipeStep,
    StepResultRoute,
)
from tests.recipe.conftest import VALID_RECIPE, _write_yaml


def test_load_recipe_smoke() -> None:
    """load_recipe(path) returns a Recipe with correct name."""
    path = builtin_recipes_dir() / "implementation.yaml"
    recipe = load_recipe(path)
    assert recipe.name == "implementation"


def test_parse_recipe_accepts_raw_dict() -> None:
    """_parse_recipe accepts a raw dict and returns a Recipe."""
    recipe = _parse_recipe({"name": "test", "steps": {"step1": {"tool": "run_cmd"}}})
    assert recipe.name == "test"


def test_iter_steps_with_context_empty_for_first_step() -> None:
    """iter_steps_with_context yields frozenset() for the first step."""
    from autoskillit.recipe.io import iter_steps_with_context

    recipe = _parse_recipe(
        {
            "name": "test",
            "steps": {
                "step1": {"tool": "run_cmd", "on_success": "done"},
                "done": {"action": "stop", "message": "ok"},
            },
        }
    )
    first_name, first_step, ctx = next(iter_steps_with_context(recipe))
    assert ctx == frozenset()


def test_iter_steps_with_context_accumulates_captures() -> None:
    """iter_steps_with_context accumulates captures from preceding steps."""
    from autoskillit.recipe.io import iter_steps_with_context

    recipe = _parse_recipe(
        {
            "name": "test",
            "steps": {
                "impl": {
                    "tool": "run_skill",
                    "capture": {"worktree_path": "${{ result.worktree_path }}"},
                    "on_success": "check",
                },
                "check": {
                    "tool": "test_check",
                    "with": {"worktree_path": "${{ context.worktree_path }}"},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "ok"},
            },
        }
    )
    steps = list(iter_steps_with_context(recipe))
    # First step: no captures yet
    assert steps[0][2] == frozenset()
    # Second step: worktree_path should be available from impl's capture
    assert steps[1][2] == frozenset({"worktree_path"})


def test_find_recipe_by_name_returns_none_for_unknown(tmp_path: Path) -> None:
    """find_recipe_by_name returns None when the recipe name does not exist."""
    from autoskillit.recipe.io import find_recipe_by_name

    result = find_recipe_by_name("nonexistent_xyz_recipe_abc", tmp_path)
    assert result is None


class TestRecipeParser:
    # WF1
    def test_load_valid_recipe(self, tmp_path: Path) -> None:
        f = _write_yaml(tmp_path / "recipe.yaml", VALID_RECIPE)
        wf = load_recipe(f)
        assert wf.name == "test-recipe"
        assert wf.description == "A test recipe"
        assert "test_dir" in wf.ingredients
        assert wf.ingredients["test_dir"].required is True
        assert wf.ingredients["branch"].default == "main"
        assert "run_tests" in wf.steps
        assert wf.steps["run_tests"].tool == "test_check"
        assert wf.steps["run_tests"].with_args["worktree_path"] == "${{ inputs.test_dir }}"
        assert wf.steps["done"].action == "stop"

    # WF4
    def test_ingredient_defaults_applied(self, tmp_path: Path) -> None:
        f = _write_yaml(tmp_path / "recipe.yaml", VALID_RECIPE)
        wf = load_recipe(f)
        assert wf.ingredients["branch"].default == "main"
        assert wf.ingredients["branch"].required is False

    # WF8
    def test_project_recipe_overrides_builtin(self, tmp_path: Path) -> None:
        wf_dir = tmp_path / ".autoskillit" / "recipes"
        wf_dir.mkdir(parents=True)
        override = {**VALID_RECIPE, "name": "implementation", "description": "Custom override"}
        _write_yaml(wf_dir / "implementation.yaml", override)

        recipes = list_recipes(tmp_path).items
        match = next(w for w in recipes if w.name == "implementation")
        assert match.source == RecipeSource.PROJECT
        assert match.description == "Custom override"

    # WF9
    def test_step_with_retries_parsed(self, tmp_path: Path) -> None:
        data = {
            "name": "retry-recipe",
            "description": "Has retry",
            "kitchen_rules": ["test"],
            "steps": {
                "impl": {
                    "tool": "run_skill",
                    "retries": 5,
                    "on_exhausted": "fail",
                    "on_success": "done",
                },
                "fail": {"action": "stop", "message": "Failed."},
                "done": {"action": "stop", "message": "Done."},
            },
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        assert wf.steps["impl"].retries == 5
        assert wf.steps["impl"].on_exhausted == "fail"

    def test_step_retries_default(self, tmp_path: Path) -> None:
        """Steps without retries/on_exhausted/on_context_limit get defaults."""
        data = {
            "name": "defaults-recipe",
            "description": "test",
            "kitchen_rules": ["test"],
            "steps": {
                "impl": {"tool": "run_skill", "on_success": "done"},
                "done": {"action": "stop", "message": "Done."},
            },
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        assert wf.steps["impl"].retries == 3
        assert wf.steps["impl"].on_exhausted == "escalate"
        assert wf.steps["impl"].on_context_limit is None

    def test_step_on_context_limit_parsed(self, tmp_path: Path) -> None:
        """on_context_limit is parsed from YAML step."""
        data = {
            "name": "ctx-limit-recipe",
            "description": "test",
            "kitchen_rules": ["test"],
            "steps": {
                "impl": {
                    "tool": "run_skill",
                    "retries": 0,
                    "on_context_limit": "retry_worktree",
                    "on_success": "done",
                },
                "retry_worktree": {"tool": "run_skill", "on_success": "done"},
                "done": {"action": "stop", "message": "Done."},
            },
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        assert wf.steps["impl"].on_context_limit == "retry_worktree"

    def test_no_retry_block_in_yaml(self, tmp_path: Path) -> None:
        """Old retry: block must raise — unrecognised field."""
        data = {
            "name": "old-retry",
            "description": "test",
            "kitchen_rules": ["test"],
            "steps": {
                "impl": {
                    "tool": "run_skill",
                    "retry": {"max_attempts": 3, "on": "needs_retry"},
                    "on_success": "done",
                },
                "done": {"action": "stop", "message": "Done."},
            },
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        with pytest.raises(Exception):
            load_recipe(f)

    def test_load_recipe_rejects_non_dict(self, tmp_path: Path) -> None:
        """YAML that parses to a non-dict must raise ValueError."""
        path = tmp_path / "list.yaml"
        path.write_text("- item1\n- item2\n")
        with pytest.raises(ValueError, match="YAML mapping"):
            load_recipe(path)

    def test_list_recipes_reports_malformed_files(self, tmp_path: Path) -> None:
        """Malformed recipe files must produce error reports."""
        wf_dir = tmp_path / ".autoskillit" / "recipes"
        wf_dir.mkdir(parents=True)
        (wf_dir / "broken.yaml").write_text("{invalid: [unclosed\n")
        result = list_recipes(tmp_path)
        assert len(result.errors) >= 1

    # WF_SUM1
    def test_recipe_summary_defaults_to_empty(self) -> None:
        wf = Recipe(name="test", description="desc")
        assert wf.summary == ""

    # WF_SUM2
    def test_parse_recipe_extracts_summary(self, tmp_path: Path) -> None:
        data = {**VALID_RECIPE, "summary": "run tests then merge"}
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        assert wf.summary == "run tests then merge"

    # WF_SUM3
    def test_builtin_recipes_summary_is_str(self) -> None:
        bd = builtin_recipes_dir()
        for f in bd.glob("*.yaml"):
            wf = load_recipe(f)
            assert isinstance(wf.summary, str), f"{f.name}: summary is not str"

    def test_python_step_parsed(self, tmp_path: Path) -> None:
        data = {
            "name": "py-recipe",
            "description": "Has python step",
            "kitchen_rules": ["test"],
            "steps": {
                "check": {
                    "python": "mymod.check_fn",
                    "on_success": "done",
                    "on_failure": "fail",
                },
                "done": {"action": "stop", "message": "OK"},
                "fail": {"action": "stop", "message": "Failed"},
            },
        }
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        assert wf.steps["check"].python == "mymod.check_fn"
        assert wf.steps["check"].tool is None
        assert wf.steps["check"].action is None

    # CAP1
    def test_capture_field_parsed(self, tmp_path: Path) -> None:
        data = {
            "name": "cap-recipe",
            "description": "Capture test",
            "kitchen_rules": ["test"],
            "steps": {
                "run": {
                    "tool": "run_skill",
                    "with": {"cwd": "/tmp"},
                    "capture": {"worktree_path": "${{ result.worktree_path }}"},
                },
                "done": {"action": "stop", "message": "ok"},
            },
        }
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        assert wf.steps["run"].capture == {"worktree_path": "${{ result.worktree_path }}"}

    # CAP2
    def test_capture_defaults_empty(self, tmp_path: Path) -> None:
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", VALID_RECIPE))
        for step in wf.steps.values():
            assert step.capture == {}

    # T4
    def test_recipe_skill_commands_are_namespaced(self) -> None:
        import autoskillit

        wf_dir = Path(autoskillit.__file__).parent / "recipes"
        for wf_path in wf_dir.glob("*.yaml"):
            content = wf_path.read_text()
            for match in re.finditer(r'skill_command:\s*"(/\S+)', content):
                ref = match.group(1)
                if "${{" in ref:
                    continue
                assert ref.startswith("/autoskillit:"), (
                    f"{wf_path.name}: {ref} should use /autoskillit: namespace"
                )

    # T_OR1
    def test_on_result_parsed(self, tmp_path: Path) -> None:
        data = {
            "name": "result-recipe",
            "description": "Has on_result",
            "kitchen_rules": ["test"],
            "steps": {
                "classify": {
                    "tool": "classify_fix",
                    "on_result": {
                        "field": "restart_scope",
                        "routes": {
                            "full_restart": "investigate",
                            "partial_restart": "implement",
                        },
                    },
                    "on_failure": "escalate",
                },
                "investigate": {"action": "stop", "message": "Investigating."},
                "implement": {"action": "stop", "message": "Implementing."},
                "escalate": {"action": "stop", "message": "Escalating."},
            },
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        assert wf.steps["classify"].on_result is not None
        assert isinstance(wf.steps["classify"].on_result, StepResultRoute)
        assert wf.steps["classify"].on_result.field == "restart_scope"
        assert wf.steps["classify"].on_result.routes == {
            "full_restart": "investigate",
            "partial_restart": "implement",
        }

    # T_OR9
    def test_on_result_defaults_to_none(self, tmp_path: Path) -> None:
        f = _write_yaml(tmp_path / "recipe.yaml", VALID_RECIPE)
        wf = load_recipe(f)
        assert wf.steps["run_tests"].on_result is None

    def test_on_result_list_format_parsed_as_conditions(self, tmp_path: Path) -> None:
        """List-format on_result parses into StepResultRoute with conditions list."""

        data = {
            "name": "predicate-recipe",
            "description": "Uses predicate on_result",
            "kitchen_rules": ["test"],
            "steps": {
                "merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                    "on_result": [
                        {"when": "result.failed_step == 'test_gate'", "route": "assess"},
                        {"when": "result.error", "route": "cleanup"},
                        {"route": "push"},
                    ],
                },
                "assess": {"action": "stop", "message": "Assess."},
                "cleanup": {"action": "stop", "message": "Cleanup."},
                "push": {"action": "stop", "message": "Push."},
            },
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        step = wf.steps["merge"]
        assert step.on_result is not None
        assert isinstance(step.on_result, StepResultRoute)
        assert len(step.on_result.conditions) == 3
        assert step.on_result.conditions[0].when == "result.failed_step == 'test_gate'"
        assert step.on_result.conditions[0].route == "assess"
        assert step.on_result.conditions[1].when == "result.error"
        assert step.on_result.conditions[1].route == "cleanup"
        assert step.on_result.conditions[2].when is None
        assert step.on_result.conditions[2].route == "push"

    def test_on_result_list_without_when_is_default_condition(self, tmp_path: Path) -> None:
        """A list entry with only route (no when key) parses as when=None (default)."""
        data = {
            "name": "default-cond-recipe",
            "description": "Default condition",
            "kitchen_rules": ["test"],
            "steps": {
                "merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                    "on_result": [{"route": "push"}],
                },
                "push": {"action": "stop", "message": "Push."},
            },
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        step = wf.steps["merge"]
        assert step.on_result is not None
        assert len(step.on_result.conditions) == 1
        assert step.on_result.conditions[0].when is None
        assert step.on_result.conditions[0].route == "push"

    def test_on_result_list_format_field_and_routes_empty(self, tmp_path: Path) -> None:
        """When list-format is used, field == '' and routes == {}."""
        data = {
            "name": "list-empty-legacy-recipe",
            "description": "List format clears legacy fields",
            "kitchen_rules": ["test"],
            "steps": {
                "merge": {
                    "tool": "merge_worktree",
                    "with": {"worktree_path": "/tmp/wt", "base_branch": "main"},
                    "on_result": [
                        {"when": "result.error", "route": "cleanup"},
                        {"route": "push"},
                    ],
                },
                "cleanup": {"action": "stop", "message": "Cleanup."},
                "push": {"action": "stop", "message": "Push."},
            },
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        step = wf.steps["merge"]
        assert step.on_result is not None
        assert step.on_result.field == ""
        assert step.on_result.routes == {}

    # CON2
    def test_parse_recipe_extracts_kitchen_rules(self, tmp_path: Path) -> None:
        data = {
            **VALID_RECIPE,
            "kitchen_rules": [
                "ONLY use AutoSkillit MCP tools",
                "NEVER use Edit, Write, Read",
            ],
        }
        wf = load_recipe(_write_yaml(tmp_path / "recipe.yaml", data))
        assert wf.kitchen_rules == [
            "ONLY use AutoSkillit MCP tools",
            "NEVER use Edit, Write, Read",
        ]

    # OPT2
    def test_parse_step_preserves_optional(self) -> None:
        step_with = _parse_step({"tool": "test_check", "optional": True})
        assert step_with.optional is True

        step_without = _parse_step({"tool": "test_check"})
        assert step_without.optional is False

    # MOD2
    def test_parse_step_extracts_model(self) -> None:
        step = _parse_step({"tool": "run_skill", "model": "sonnet"})
        assert step.model == "sonnet"

    # MOD3
    def test_parse_step_model_absent(self) -> None:
        step = _parse_step({"tool": "run_skill"})
        assert step.model is None

    # MOD4
    def test_bundled_resolve_failures_steps_use_config_default(self) -> None:
        bd = builtin_recipes_dir()
        for f in bd.glob("*.yaml"):
            wf = load_recipe(f)
            for step_name, step in wf.steps.items():
                if (
                    step.with_args.get("skill_command")
                    and "resolve-failures" in step.with_args["skill_command"]
                ):
                    assert step.model is None, (
                        f"{f.name} step '{step_name}' should not have explicit model "
                        "(sonnet is the config default)"
                    )


class TestListRecipes:
    """TestListRecipes: discovery from project and builtin sources."""

    def test_finds_builtins(self, tmp_path: Path) -> None:
        result = list_recipes(tmp_path)
        recipes = result.items
        names = {w.name for w in recipes}
        assert "implementation" in names
        assert len(recipes) > 0
        assert all(r.source.value in ("project", "builtin") for r in recipes)

    def test_list_recipes_bundled_appear_before_project(self, tmp_path: Path) -> None:
        """Bundled recipes must appear before project recipes in list_recipes() output."""
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        # Write a project recipe whose name would sort before bundled "implementation"
        (recipes_dir / "aardvark.yaml").write_text(
            "name: aardvark\ndescription: test\nsteps: {}\n"
        )
        result = list_recipes(tmp_path)
        sources = [r.source for r in result.items]
        # All BUILTIN items must precede all PROJECT items
        seen_project = False
        for source in sources:
            if source == RecipeSource.PROJECT:
                seen_project = True
            elif source == RecipeSource.BUILTIN:
                assert not seen_project, (
                    "A BUILTIN recipe appeared after a PROJECT recipe — ordering is broken"
                )

    def test_list_recipes_alphabetical_within_bundled_tier(self, tmp_path: Path) -> None:
        """Bundled recipes must be sorted alphabetically by name within their tier."""
        result = list_recipes(tmp_path)
        builtin_names = [r.name for r in result.items if r.source == RecipeSource.BUILTIN]
        assert builtin_names == sorted(builtin_names), (
            f"Bundled recipes not in alphabetical order: {builtin_names}"
        )

    def test_list_recipes_alphabetical_within_project_tier(self, tmp_path: Path) -> None:
        """Project recipes must be sorted alphabetically by name within their tier."""
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        for name in ("zebra", "apple", "mango"):
            (recipes_dir / f"{name}.yaml").write_text(
                f"name: {name}\ndescription: test\nsteps: {{}}\n"
            )
        result = list_recipes(tmp_path)
        project_names = [r.name for r in result.items if r.source == RecipeSource.PROJECT]
        assert project_names == sorted(project_names), (
            f"Project recipes not in alphabetical order: {project_names}"
        )


class TestBuiltinRecipesDir:
    """Tests for builtin_recipes_dir() function."""

    def test_returns_existing_directory(self) -> None:
        d = builtin_recipes_dir()
        assert d.is_dir(), f"builtin_recipes_dir() {d} is not a directory"

    def test_points_to_recipes(self) -> None:
        d = builtin_recipes_dir()
        assert d.name == "recipes", (
            f"builtin_recipes_dir() should point to 'recipes', got '{d.name}'"
        )

    def test_contains_yaml_files(self) -> None:
        d = builtin_recipes_dir()
        yaml_files = list(d.glob("*.yaml"))
        assert len(yaml_files) > 0, "builtin_recipes_dir() contains no YAML files"


class TestVersionField:
    """autoskillit_version field on Recipe dataclass."""

    # VER1+VER2 merged
    @pytest.mark.parametrize("version_val,expected", [(None, None), ("0.2.0", "0.2.0")])
    def test_version_field(self, version_val, expected) -> None:
        data = {
            "name": "v-test",
            "description": "d",
            "steps": {
                "do_it": {"tool": "run_cmd", "on_success": "done"},
                "done": {"action": "stop", "message": "Done."},
            },
        }
        if version_val is not None:
            data["autoskillit_version"] = version_val
        wf = _parse_recipe(data)
        assert wf.version == expected

    # VER4
    def test_version_preserved_in_round_trip(self, tmp_path: Path) -> None:
        data = {
            "name": "version-test-recipe",
            "description": "A recipe for testing the version field",
            "kitchen_rules": ["Only use AutoSkillit MCP tools during pipeline execution"],
            "steps": {
                "do_it": {"tool": "run_cmd", "on_success": "done"},
                "done": {"action": "stop", "message": "Done."},
            },
            "autoskillit_version": "1.3.0",
        }
        path = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(path)
        assert wf.version == "1.3.0"


# ---------------------------------------------------------------------------
# capture_list field tests (D1–D3, D8–D9)
# ---------------------------------------------------------------------------


# D1
def test_recipe_step_accepts_capture_list_field() -> None:
    """RecipeStep accepts capture_list field and stores it."""
    step = RecipeStep(
        tool="run_skill",
        with_args={"skill_command": "/autoskillit:make-plan inputs.task"},
        capture={"plan_path": "${{ result.plan_path }}"},
        capture_list={"plan_parts": "${{ result.plan_parts }}"},
        on_success="verify",
    )
    assert step.capture_list == {"plan_parts": "${{ result.plan_parts }}"}


# D2
def test_recipe_step_capture_list_defaults_empty() -> None:
    """RecipeStep.capture_list defaults to an empty dict."""
    step = RecipeStep(tool="run_skill", with_args={}, on_success="done")
    assert step.capture_list == {}


# D3
def test_recipe_yaml_with_capture_list_parses(tmp_path: Path) -> None:
    """YAML recipe with capture_list key is parsed into RecipeStep.capture_list."""
    data = {
        "name": "test-recipe",
        "description": "test",
        "ingredients": {},
        "steps": {
            "plan": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:make-plan inputs.task"},
                "capture": {"plan_path": "${{ result.plan_path }}"},
                "capture_list": {"plan_parts": "${{ result.plan_parts }}"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done"},
        },
    }
    path = _write_yaml(tmp_path / "recipe.yaml", data)
    recipe = load_recipe(path)
    assert recipe.steps["plan"].capture_list == {"plan_parts": "${{ result.plan_parts }}"}


# D8
def test_iter_steps_with_context_includes_capture_list_keys() -> None:
    """iter_steps_with_context must include capture_list keys in available_context."""
    from autoskillit.recipe.io import iter_steps_with_context

    recipe = Recipe(
        name="test",
        description="test",
        ingredients={},
        steps={
            "plan": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:make-plan t"},
                capture={"plan_path": "${{ result.plan_path }}"},
                capture_list={"plan_parts": "${{ result.plan_parts }}"},
                on_success="verify",
            ),
            "verify": RecipeStep(
                tool="run_skill",
                with_args={"skill_command": "/autoskillit:dry-walkthrough c"},
                on_success="done",
            ),
            "done": RecipeStep(action="stop", message="Done"),
        },
        kitchen_rules=[],
    )
    steps = list(iter_steps_with_context(recipe))
    verify_ctx = next(ctx for name, _, ctx in steps if name == "verify")
    assert "plan_parts" in verify_ctx, (
        "capture_list keys must appear in available_context for downstream steps"
    )


# D9
def test_implementation_pipeline_captures_plan_parts_as_list() -> None:
    """implementation.yaml plan step must capture plan_parts via capture_list."""
    recipe = load_recipe(builtin_recipes_dir() / "implementation.yaml")
    step = recipe.steps["plan"]
    assert hasattr(step, "capture_list"), "RecipeStep must have capture_list field"
    assert "plan_parts" in step.capture_list, (
        "implementation plan step must capture plan_parts via capture_list"
    )


# IO-1: RecipeInfo dataclass accepts content kwarg; defaults to None
def test_recipe_info_has_content_field_defaulting_to_none() -> None:
    """RecipeInfo.content defaults to None when not provided."""
    from autoskillit.recipe.schema import RecipeInfo

    info = RecipeInfo(
        name="x",
        description="y",
        source=RecipeSource.BUILTIN,
        path=Path("/x.yaml"),
    )
    assert info.content is None


# IO-2: list_recipes populates content field with raw YAML text
def test_list_recipes_populates_content(tmp_path: Path) -> None:
    """list_recipes() populates the content field with raw YAML text."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    raw = "name: my-recipe\ndescription: test\nsteps: {}\n"
    (recipes_dir / "my-recipe.yaml").write_text(raw)
    result = list_recipes(tmp_path)
    assert result.items, "expected at least one recipe"
    item = next(r for r in result.items if r.name == "my-recipe")
    assert item.content == raw


# IO-3: content field preserved through find_recipe_by_name
def test_find_recipe_by_name_returns_info_with_content(tmp_path: Path) -> None:
    """find_recipe_by_name() returns a RecipeInfo with content populated."""
    from autoskillit.recipe.io import find_recipe_by_name

    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    raw = "name: my-recipe\ndescription: test\nsteps: {}\n"
    (recipes_dir / "my-recipe.yaml").write_text(raw)
    info = find_recipe_by_name("my-recipe", tmp_path)
    assert info is not None
    assert info.content == raw


# ---------------------------------------------------------------------------
# Bundled recipe skill_command prefix contract
# ---------------------------------------------------------------------------


def test_parse_step_with_key_maps_to_with_args() -> None:
    """_parse_step maps YAML 'with' key to RecipeStep.with_args."""
    step = _parse_step({"tool": "claim_issue", "with": {"issue_url": "https://example.com"}})
    assert step.with_args == {"issue_url": "https://example.com"}


def test_parse_step_with_args_key_is_not_read() -> None:
    """_parse_step does NOT read 'with_args' YAML key — confirms the fix is needed."""
    step = _parse_step({"tool": "claim_issue", "with_args": {"issue_url": "https://example.com"}})
    assert step.with_args == {}, "with_args YAML key must not be read — use 'with:' instead"


# ---------------------------------------------------------------------------
# P9-F1: step description field mapping
# ---------------------------------------------------------------------------


def test_parse_step_maps_description_field() -> None:
    """_parse_step maps 'description' YAML key to RecipeStep.description."""
    step = _parse_step({"tool": "run_cmd", "description": "Run the build"})
    assert step.description == "Run the build"


def test_parse_step_description_defaults_to_empty_string() -> None:
    """_parse_step sets description to '' when YAML key absent."""
    step = _parse_step({"tool": "run_cmd"})
    assert step.description == ""


def test_load_recipe_preserves_step_description(tmp_path: Path) -> None:
    """End-to-end: load_recipe preserves description: on a step."""
    import textwrap

    yaml_content = textwrap.dedent("""\
        name: desc-test
        kitchen_rules: [rule1]
        steps:
          build:
            tool: run_cmd
            description: Run the full build suite
            with:
              cmd: make all
    """)
    recipe_file = tmp_path / "desc-test.yaml"
    recipe_file.write_text(yaml_content)
    recipe = load_recipe(recipe_file)
    assert recipe.steps["build"].description == "Run the full build suite"


# ---------------------------------------------------------------------------
# CC-F4: kitchen_rules non-list raises ValueError
# ---------------------------------------------------------------------------


def test_parse_recipe_kitchen_rules_string_raises() -> None:
    """_parse_recipe raises ValueError when kitchen_rules is a string."""
    with pytest.raises(ValueError, match="kitchen_rules"):
        _parse_recipe(
            {
                "name": "bad",
                "kitchen_rules": "not-a-list",
                "steps": {"s": {"tool": "run_cmd"}},
            }
        )


def test_parse_recipe_kitchen_rules_dict_raises() -> None:
    """_parse_recipe raises ValueError when kitchen_rules is a dict."""
    with pytest.raises(ValueError, match="kitchen_rules"):
        _parse_recipe(
            {
                "name": "bad",
                "kitchen_rules": {"rule": "val"},
                "steps": {"s": {"tool": "run_cmd"}},
            }
        )


def test_parse_recipe_kitchen_rules_int_raises() -> None:
    """_parse_recipe raises ValueError when kitchen_rules is an int."""
    with pytest.raises(ValueError, match="kitchen_rules"):
        _parse_recipe(
            {
                "name": "bad",
                "kitchen_rules": 42,
                "steps": {"s": {"tool": "run_cmd"}},
            }
        )


def test_parse_recipe_kitchen_rules_absent_gives_empty_list() -> None:
    """_parse_recipe produces kitchen_rules=[] when field absent (existing behavior preserved)."""
    recipe = _parse_recipe(
        {
            "name": "ok",
            "steps": {"s": {"tool": "run_cmd"}},
        }
    )
    assert recipe.kitchen_rules == []


def test_parse_recipe_kitchen_rules_valid_list_accepted() -> None:
    """_parse_recipe accepts a valid list kitchen_rules."""
    recipe = _parse_recipe(
        {
            "name": "ok",
            "kitchen_rules": ["rule1", "rule2"],
            "steps": {"s": {"tool": "run_cmd"}},
        }
    )
    assert recipe.kitchen_rules == ["rule1", "rule2"]


def test_bundled_recipes_all_skill_commands_start_with_slash() -> None:
    """All run_skill steps in bundled recipes must have
    skill_command starting with '/' after smoke-task migration."""
    from autoskillit.core.types import SKILL_COMMAND_PREFIX, SKILL_TOOLS

    violations: list[str] = []
    for yaml_path in builtin_recipes_dir().glob("*.yaml"):
        recipe = load_recipe(yaml_path)
        for step_name, step in recipe.steps.items():
            if step.tool in SKILL_TOOLS:
                sc = step.with_args.get("skill_command", SKILL_COMMAND_PREFIX)
                if not sc.strip().startswith(SKILL_COMMAND_PREFIX):
                    violations.append(f"{yaml_path.name}:{step_name}: {sc!r}")
    assert not violations, f"Bundled recipe steps with prose skill_command: {violations}"


# ---------------------------------------------------------------------------
# T1 — schema-drift guard
# ---------------------------------------------------------------------------


def test_parse_step_handles_all_recipe_step_fields() -> None:
    """_PARSE_STEP_HANDLED_FIELDS must equal RecipeStep.__dataclass_fields__ keys."""
    from autoskillit.recipe.io import _PARSE_STEP_HANDLED_FIELDS
    from autoskillit.recipe.schema import RecipeStep

    schema_fields = frozenset(RecipeStep.__dataclass_fields__)
    assert _PARSE_STEP_HANDLED_FIELDS == schema_fields, (
        f"_parse_step field list is out of sync.\n"
        f"  Missing from handled: {schema_fields - _PARSE_STEP_HANDLED_FIELDS}\n"
        f"  Extra in handled:     {_PARSE_STEP_HANDLED_FIELDS - schema_fields}"
    )


# ---------------------------------------------------------------------------
# T2 — _path_mtime_ns replaces the two old helpers
# ---------------------------------------------------------------------------


def test_path_mtime_ns_exists_and_old_helpers_removed() -> None:
    """recipe/_api.py must expose _path_mtime_ns; _file_mtime_ns/_dir_mtime_ns removed."""
    import autoskillit.recipe._api as api

    assert hasattr(api, "_path_mtime_ns"), "_path_mtime_ns must exist"
    assert not hasattr(api, "_file_mtime_ns"), "_file_mtime_ns must be removed"
    assert not hasattr(api, "_dir_mtime_ns"), "_dir_mtime_ns must be removed"


# ---------------------------------------------------------------------------
# REQ-ORD-003: Stable positions when project recipe added
# ---------------------------------------------------------------------------


def test_list_recipes_stable_with_project_recipe_added(tmp_path: Path) -> None:
    """Adding a project recipe must not shift the positions of bundled recipes."""
    # Collect bundled positions without any project recipes
    before = [r.name for r in list_recipes(tmp_path).items]

    # Add a project recipe whose name sorts before all bundled recipes
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    (recipes_dir / "aaa-custom.yaml").write_text(
        "name: aaa-custom\ndescription: test\nsteps: {}\n"
    )
    after = [r.name for r in list_recipes(tmp_path).items]

    # Bundled names must occupy the same leading positions
    bundled_before = [n for n in before]
    bundled_after = [n for n in after if n in set(bundled_before)]
    assert bundled_after == bundled_before, (
        "Adding a project recipe must not shift bundled recipe positions"
    )
