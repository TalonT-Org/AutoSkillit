"""Tests for recipe discovery from .autoskillit/recipes/."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autoskillit.recipe_loader import (
    _extract_frontmatter,
    _parse_recipe_metadata,
    list_recipes,
    load_recipe,
    sync_bundled_recipes,
)
from autoskillit.recipe_parser import builtin_recipes_dir

SCRIPT_A = {
    "name": "implementation",
    "description": "Plan and implement a task end-to-end.",
    "summary": "make-plan > review > for each part: dry-walk > implement > test > merge",
    "ingredients": {
        "task": {"description": "What to implement", "required": True},
        "base_branch": {"description": "Branch to merge into", "default": "main"},
    },
    "steps": {
        "plan": {
            "tool": "run_skill",
            "with": {"skill_command": "/autoskillit:make-plan ${{ inputs.task }}", "cwd": "."},
            "on_success": "done",
            "on_failure": "escalate",
        },
        "done": {"action": "stop", "message": "Done."},
        "escalate": {"action": "stop", "message": "Failed."},
    },
}

SCRIPT_B = {
    "name": "investigate-fix",
    "description": "Investigate and fix a bug.",
    "ingredients": {
        "problem": {"description": "Error description", "required": True},
    },
    "steps": {
        "investigate": {
            "tool": "run_skill",
            "with": {
                "skill_command": "/autoskillit:investigate ${{ inputs.problem }}",
                "cwd": ".",
            },
            "on_success": "done",
            "on_failure": "escalate",
        },
        "done": {"action": "stop", "message": "Done."},
        "escalate": {"action": "stop", "message": "Failed."},
    },
}


def _make_recipes_dir(tmp_path: Path) -> Path:
    """Create .autoskillit/recipes/ with two test YAML files."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    (recipes_dir / "implementation.yaml").write_text(yaml.dump(SCRIPT_A, default_flow_style=False))
    (recipes_dir / "investigate.yaml").write_text(yaml.dump(SCRIPT_B, default_flow_style=False))
    return recipes_dir


class TestListRecipes:
    # SL1
    def test_empty_when_dir_missing(self, tmp_path: Path) -> None:
        """list_recipes returns empty result when .autoskillit/recipes/ doesn't exist."""
        result = list_recipes(tmp_path)
        assert result.items == []
        assert result.errors == []

    # SL2
    def test_discovers_yaml_files(self, tmp_path: Path) -> None:
        """list_recipes discovers .yaml files in .autoskillit/recipes/."""
        _make_recipes_dir(tmp_path)
        recipes = list_recipes(tmp_path).items
        names = {s.name for s in recipes}
        assert "implementation" in names
        assert "investigate-fix" in names

    # SL3
    def test_ignores_non_yaml_and_reports_malformed(self, tmp_path: Path) -> None:
        """list_recipes ignores non-yaml files and reports malformed yaml as errors."""
        recipes_dir = _make_recipes_dir(tmp_path)
        (recipes_dir / "readme.txt").write_text("not a yaml recipe")
        (recipes_dir / "broken.yaml").write_text(":: invalid yaml {{[")
        result = list_recipes(tmp_path)
        names = {s.name for s in result.items}
        assert "readme" not in names
        assert "broken" not in names
        assert len(result.items) == 2  # only the two valid ones
        assert len(result.errors) == 1  # broken.yaml reported
        assert "broken.yaml" in result.errors[0].path.name

    # SL4
    def test_extracts_summary_field(self, tmp_path: Path) -> None:
        """list_recipes extracts summary field from YAML."""
        _make_recipes_dir(tmp_path)
        recipes = list_recipes(tmp_path).items
        impl = next(s for s in recipes if s.name == "implementation")
        assert impl.summary == SCRIPT_A["summary"]

    # SL5
    def test_empty_summary_when_absent(self, tmp_path: Path) -> None:
        """list_recipes returns empty summary when field absent."""
        _make_recipes_dir(tmp_path)
        recipes = list_recipes(tmp_path).items
        inv = next(s for s in recipes if s.name == "investigate-fix")
        assert inv.summary == ""

    # SL8
    def test_sorted_by_name(self, tmp_path: Path) -> None:
        """list_recipes sorts results by name."""
        _make_recipes_dir(tmp_path)
        recipes = list_recipes(tmp_path).items
        names = [s.name for s in recipes]
        assert names == sorted(names)

    def test_discovers_frontmatter_format(self, tmp_path: Path) -> None:
        """Recipes in YAML frontmatter format must be discovered."""
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "pipeline.yaml").write_text(
            "---\nname: my-pipeline\ndescription: A pipeline\n"
            "summary: plan > implement\n---\n\n# Pipeline\nDo stuff.\n"
        )
        result = list_recipes(tmp_path)
        assert len(result.items) == 1
        assert result.items[0].name == "my-pipeline"
        assert result.items[0].summary == "plan > implement"

    def test_discovers_frontmatter_with_adversarial_body(self, tmp_path: Path) -> None:
        """Recipes with YAML-like Markdown bodies must be discovered, not errored."""
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "pipeline.yaml").write_text(
            "---\nname: adv-pipeline\ndescription: Test\n---\n\n"
            "# Steps\n\nSETUP:\n  - item: value\n  - key: other\n"
        )
        result = list_recipes(tmp_path)
        assert len(result.items) == 1
        assert len(result.errors) == 0
        assert result.items[0].name == "adv-pipeline"

    def test_reports_errors(self, tmp_path: Path) -> None:
        """Malformed recipes must produce error reports, not silent skips."""
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "good.yaml").write_text("name: good\ndescription: Valid\n")
        (recipes_dir / "bad.yaml").write_text(":: invalid {{[\n")
        result = list_recipes(tmp_path)
        assert len(result.items) == 1
        assert len(result.errors) == 1
        assert "bad.yaml" in result.errors[0].path.name

    def test_list_recipes_discovers_from_recipes_dir(self, tmp_path: Path) -> None:
        """list_recipes discovers from .autoskillit/recipes/ not scripts/."""
        _make_recipes_dir(tmp_path)
        result = list_recipes(tmp_path)
        assert len(result.items) > 0

    def test_recipe_info_has_source_field(self, tmp_path: Path) -> None:
        """RecipeInfo has a source field set to RecipeSource.PROJECT."""
        from autoskillit.recipe_parser import RecipeSource

        _make_recipes_dir(tmp_path)
        result = list_recipes(tmp_path)
        for item in result.items:
            assert item.source == RecipeSource.PROJECT

    def test_load_recipe_raw_returns_yaml(self, tmp_path: Path) -> None:
        """load_recipe returns raw YAML string."""
        _make_recipes_dir(tmp_path)
        content = load_recipe(tmp_path, "implementation")
        assert content is not None
        assert isinstance(content, str)
        data = yaml.safe_load(content)
        assert data["name"] == "implementation"

    def test_list_recipes_empty_when_dir_missing(self, tmp_path: Path) -> None:
        """list_recipes returns empty result when .autoskillit/recipes/ missing."""
        result = list_recipes(tmp_path)
        assert result.items == []
        assert result.errors == []


class TestParseRecipeMetadata:
    def test_single_document(self, tmp_path: Path) -> None:
        """Standard YAML without frontmatter."""
        path = tmp_path / "recipe.yaml"
        path.write_text("name: my-recipe\ndescription: A recipe\nsummary: do stuff\n")
        info = _parse_recipe_metadata(path)
        assert info.name == "my-recipe"
        assert info.description == "A recipe"
        assert info.summary == "do stuff"

    def test_frontmatter_format(self, tmp_path: Path) -> None:
        """YAML frontmatter with --- delimiters and Markdown body."""
        path = tmp_path / "recipe.yaml"
        path.write_text(
            "---\nname: fm-recipe\ndescription: Frontmatter\n---\n\n"
            "# Title\n\nKey: value\n- list item\n"
        )
        info = _parse_recipe_metadata(path)
        assert info.name == "fm-recipe"
        assert info.description == "Frontmatter"

    def test_frontmatter_with_steps(self, tmp_path: Path) -> None:
        """YAML frontmatter where metadata block includes steps."""
        path = tmp_path / "recipe.yaml"
        path.write_text(
            "---\nname: step-recipe\ndescription: Has steps\n"
            "steps:\n  plan:\n    tool: run_skill\n---\n"
        )
        info = _parse_recipe_metadata(path)
        assert info.name == "step-recipe"

    def test_frontmatter_with_yaml_like_body(self, tmp_path: Path) -> None:
        """Frontmatter parsing must succeed even when body has YAML-like syntax."""
        path = tmp_path / "recipe.yaml"
        path.write_text(
            "---\n"
            "name: pipeline\n"
            "description: A pipeline\n"
            "---\n\n"
            "# Implementation Pipeline\n\n"
            "## Phase 1: Planning\n"
            "SETUP:\n"
            "  - project_dir = /home/user/project\n"
            "  - work_dir = /home/user/work\n\n"
            "PIPELINE:\n"
            "0. Run make-plan with the task:\n"
            "   task: ${{ inputs.task }}\n"
        )
        info = _parse_recipe_metadata(path)
        assert info.name == "pipeline"
        assert info.description == "A pipeline"

    def test_rejects_empty_file(self, tmp_path: Path) -> None:
        """Empty file raises ValueError."""
        path = tmp_path / "empty.yaml"
        path.write_text("")
        with pytest.raises(ValueError, match="mapping"):
            _parse_recipe_metadata(path)

    def test_rejects_non_mapping(self, tmp_path: Path) -> None:
        """File with YAML list raises ValueError."""
        path = tmp_path / "list.yaml"
        path.write_text("- item1\n- item2\n")
        with pytest.raises(ValueError, match="mapping"):
            _parse_recipe_metadata(path)

    def test_rejects_missing_name(self, tmp_path: Path) -> None:
        """File without name field raises ValueError."""
        path = tmp_path / "noname.yaml"
        path.write_text("description: No name here\n")
        with pytest.raises(ValueError, match="name"):
            _parse_recipe_metadata(path)


class TestExtractFrontmatter:
    def test_plain_yaml_passthrough(self) -> None:
        """Text without --- prefix is returned unchanged."""
        text = "name: foo\ndescription: bar\n"
        assert _extract_frontmatter(text) == text

    def test_frontmatter_extracts_metadata(self) -> None:
        """Text between --- delimiters is extracted."""
        text = "---\nname: foo\n---\n\n# Body\n"
        assert _extract_frontmatter(text) == "name: foo"

    def test_frontmatter_discards_body(self) -> None:
        """Everything after closing --- is discarded."""
        text = "---\nname: foo\n---\n\nSETUP:\n  - bad: yaml\n"
        result = _extract_frontmatter(text)
        assert "SETUP" not in result
        assert "bad" not in result

    def test_frontmatter_missing_close_raises(self) -> None:
        """Missing closing --- raises ValueError."""
        text = "---\nname: foo\nno closing delimiter\n"
        with pytest.raises(ValueError):
            _extract_frontmatter(text)


class TestLoadRecipe:
    # SL6
    def test_returns_raw_yaml(self, tmp_path: Path) -> None:
        """load_recipe returns raw YAML content for existing recipe name."""
        _make_recipes_dir(tmp_path)
        content = load_recipe(tmp_path, "implementation")
        assert content is not None
        parsed = yaml.safe_load(content)
        assert parsed["name"] == "implementation"

    # SL7
    def test_returns_none_for_nonexistent(self, tmp_path: Path) -> None:
        """load_recipe returns None for nonexistent recipe name."""
        _make_recipes_dir(tmp_path)
        assert load_recipe(tmp_path, "nonexistent") is None


# ---------------------------------------------------------------------------
# TestRecipeVersion: RecipeInfo includes version from autoskillit_version field
# ---------------------------------------------------------------------------


class TestSyncBundledRecipes:
    def test_no_op_when_autoskillit_dir_missing(self, tmp_path: Path) -> None:
        """sync_bundled_recipes does nothing when .autoskillit/ does not exist."""
        sync_bundled_recipes(tmp_path)
        assert not (tmp_path / ".autoskillit").exists()

    def test_creates_recipes_dir_when_absent(self, tmp_path: Path) -> None:
        """sync_bundled_recipes creates .autoskillit/recipes/ if .autoskillit/ exists."""
        (tmp_path / ".autoskillit").mkdir()
        sync_bundled_recipes(tmp_path)
        assert (tmp_path / ".autoskillit" / "recipes").is_dir()

    def test_copies_bundled_recipes(self, tmp_path: Path) -> None:
        """sync_bundled_recipes copies all bundled recipe YAMLs into recipes/."""
        (tmp_path / ".autoskillit").mkdir()
        sync_bundled_recipes(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        bundled = list(builtin_recipes_dir().glob("*.yaml"))
        assert len(bundled) > 0
        for src in bundled:
            assert (recipes_dir / src.name).exists()

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        """sync_bundled_recipes overwrites same-named local recipes with bundled content."""
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        bundled = next(builtin_recipes_dir().glob("*.yaml"))
        (recipes_dir / bundled.name).write_text("name: stale\ndescription: old\n")
        sync_bundled_recipes(tmp_path)
        content = (recipes_dir / bundled.name).read_text()
        assert "stale" not in content

    def test_leaves_project_specific_recipes_untouched(self, tmp_path: Path) -> None:
        """sync_bundled_recipes does not delete or modify project-specific recipes."""
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "my-custom-recipe.yaml").write_text("name: my-custom-recipe\n")
        sync_bundled_recipes(tmp_path)
        assert (recipes_dir / "my-custom-recipe.yaml").read_text() == "name: my-custom-recipe\n"

    def test_synced_recipes_are_discoverable(self, tmp_path: Path) -> None:
        """Recipes synced from bundled recipes are returned by list_recipes."""
        (tmp_path / ".autoskillit").mkdir()
        sync_bundled_recipes(tmp_path)
        result = list_recipes(tmp_path)
        names = {s.name for s in result.items}
        bundled = list(builtin_recipes_dir().glob("*.yaml"))
        for src in bundled:
            data = yaml.safe_load(src.read_text())
            assert data["name"] in names


class TestRecipeVersion:
    """RecipeInfo includes version from autoskillit_version field."""

    # SV1: RecipeInfo.version is None when field absent
    def test_version_none_when_absent(self, tmp_path: Path) -> None:
        """_parse_recipe_metadata sets version=None when autoskillit_version is absent."""
        path = tmp_path / "recipe.yaml"
        path.write_text("name: my-recipe\ndescription: A recipe\n")
        info = _parse_recipe_metadata(path)
        assert info.version is None

    # SV2: RecipeInfo.version is "0.2.0" when field present
    def test_version_set_when_present(self, tmp_path: Path) -> None:
        """_parse_recipe_metadata reads autoskillit_version and stores it as version."""
        path = tmp_path / "recipe.yaml"
        path.write_text('name: my-recipe\ndescription: A recipe\nautoskillit_version: "0.2.0"\n')
        info = _parse_recipe_metadata(path)
        assert info.version == "0.2.0"

    # SV3: list_recipes returns version in RecipeInfo items
    def test_list_recipes_includes_version(self, tmp_path: Path) -> None:
        """list_recipes propagates autoskillit_version into the returned RecipeInfo items."""
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "versioned.yaml").write_text(
            "name: versioned-recipe\n"
            "description: Has version\n"
            'autoskillit_version: "0.2.0"\n'
            "steps:\n"
            "  do_it:\n"
            "    tool: run_cmd\n"
            "    on_success: done\n"
            "  done:\n"
            "    action: stop\n"
            "    message: Done.\n"
        )
        (recipes_dir / "unversioned.yaml").write_text(
            "name: unversioned-recipe\ndescription: No version\n"
        )
        result = list_recipes(tmp_path)
        by_name = {s.name: s for s in result.items}
        assert by_name["versioned-recipe"].version == "0.2.0"
        assert by_name["unversioned-recipe"].version is None
