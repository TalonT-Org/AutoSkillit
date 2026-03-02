"""Tests for recipe I/O and parsing (recipe_io module)."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest
import yaml

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

VALID_RECIPE = {
    "name": "test-recipe",
    "description": "A test recipe",
    "ingredients": {
        "test_dir": {"description": "Dir to test", "required": True},
        "branch": {"description": "Branch", "default": "main"},
    },
    "kitchen_rules": ["NEVER use native tools"],
    "steps": {
        "run_tests": {
            "tool": "test_check",
            "with": {"worktree_path": "${{ inputs.test_dir }}"},
            "on_success": "done",
            "on_failure": "escalate",
        },
        "done": {"action": "stop", "message": "Tests passed."},
        "escalate": {"action": "stop", "message": "Need help."},
    },
}


def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.dump(data, default_flow_style=False))
    return path


def test_recipe_parser_module_no_longer_exists() -> None:
    """recipe_parser module must be gone — ModuleNotFoundError expected."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("autoskillit.recipe_parser")


def test_load_recipe_smoke() -> None:
    """load_recipe(path) returns a Recipe with correct name."""
    path = builtin_recipes_dir() / "audit-and-fix.yaml"
    recipe = load_recipe(path)
    assert recipe.name == "audit-and-fix"


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
        override = {**VALID_RECIPE, "name": "bugfix-loop", "description": "Custom override"}
        _write_yaml(wf_dir / "bugfix-loop.yaml", override)

        recipes = list_recipes(tmp_path).items
        match = next(w for w in recipes if w.name == "bugfix-loop")
        assert match.source == RecipeSource.PROJECT
        assert match.description == "Custom override"

    # WF9
    def test_step_with_retry_parsed(self, tmp_path: Path) -> None:
        data = {
            "name": "retry-recipe",
            "description": "Has retry",
            "kitchen_rules": ["test"],
            "steps": {
                "impl": {
                    "tool": "run_skill_retry",
                    "retry": {"max_attempts": 5, "on": "needs_retry", "on_exhausted": "fail"},
                },
                "fail": {"action": "stop", "message": "Failed."},
            },
        }
        f = _write_yaml(tmp_path / "recipe.yaml", data)
        wf = load_recipe(f)
        assert wf.steps["impl"].retry is not None
        assert wf.steps["impl"].retry.max_attempts == 5
        assert wf.steps["impl"].retry.on == "needs_retry"
        assert wf.steps["impl"].retry.on_exhausted == "fail"

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
    def test_bundled_resolve_failures_steps_use_sonnet(self) -> None:
        bd = builtin_recipes_dir()
        for f in bd.glob("*.yaml"):
            wf = load_recipe(f)
            for step_name, step in wf.steps.items():
                if (
                    step.with_args.get("skill_command")
                    and "resolve-failures" in step.with_args["skill_command"]
                ):
                    assert step.model == "sonnet", (
                        f"{f.name} step '{step_name}' should have model='sonnet'"
                    )


class TestListRecipes:
    """TestListRecipes: discovery from project and builtin sources."""

    def test_finds_builtins(self, tmp_path: Path) -> None:
        result = list_recipes(tmp_path)
        recipes = result.items
        names = {w.name for w in recipes}
        assert "bugfix-loop" in names
        assert "implementation-pipeline" in names
        assert len(recipes) > 0
        assert all(r.source.value in ("project", "builtin") for r in recipes)


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

    # VER1
    def test_version_none_when_absent(self) -> None:
        data = {
            "name": "version-test-recipe",
            "description": "A recipe for testing the version field",
            "kitchen_rules": ["Only use AutoSkillit MCP tools during pipeline execution"],
            "steps": {
                "do_it": {"tool": "run_cmd", "on_success": "done"},
                "done": {"action": "stop", "message": "Done."},
            },
        }
        wf = _parse_recipe(data)
        assert wf.version is None

    # VER2
    def test_version_set_when_present(self) -> None:
        data = {
            "name": "version-test-recipe",
            "description": "A recipe for testing the version field",
            "kitchen_rules": ["Only use AutoSkillit MCP tools during pipeline execution"],
            "steps": {
                "do_it": {"tool": "run_cmd", "on_success": "done"},
                "done": {"action": "stop", "message": "Done."},
            },
            "autoskillit_version": "0.2.0",
        }
        wf = _parse_recipe(data)
        assert wf.version == "0.2.0"

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


def test_builtin_recipes_dir_points_to_recipes() -> None:
    d = builtin_recipes_dir()
    assert d.name == "recipes"


# ---------------------------------------------------------------------------
# capture_list field tests (D1–D3, D8–D9)
# ---------------------------------------------------------------------------


# D1
def test_recipe_step_accepts_capture_list_field() -> None:
    """RecipeStep accepts capture_list field and stores it."""
    step = RecipeStep(
        tool="run_skill_retry",
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
                "tool": "run_skill_retry",
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
                tool="run_skill_retry",
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
    """implementation-pipeline.yaml plan step must capture plan_parts via capture_list."""
    recipe = load_recipe(builtin_recipes_dir() / "implementation-pipeline.yaml")
    step = recipe.steps["plan"]
    assert hasattr(step, "capture_list"), "RecipeStep must have capture_list field"
    assert "plan_parts" in step.capture_list, (
        "implementation-pipeline plan step must capture plan_parts via capture_list"
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


def test_bundled_recipes_all_skill_commands_start_with_slash() -> None:
    """All run_skill/run_skill_retry steps in bundled recipes must have
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
