"""Tests for path-based recipe metadata utilities in recipe_loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe.loader import (
    _extract_frontmatter,
    parse_recipe_metadata,
)

pytestmark = [pytest.mark.layer("recipe")]


class TestParseRecipeMetadata:
    def test_single_document(self, tmp_path: Path) -> None:
        """Standard YAML without frontmatter."""
        path = tmp_path / "recipe.yaml"
        path.write_text("name: my-recipe\ndescription: A recipe\nsummary: do stuff\n")
        info = parse_recipe_metadata(path)
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
        info = parse_recipe_metadata(path)
        assert info.name == "fm-recipe"
        assert info.description == "Frontmatter"

    def test_frontmatter_with_steps(self, tmp_path: Path) -> None:
        """YAML frontmatter where metadata block includes steps."""
        path = tmp_path / "recipe.yaml"
        path.write_text(
            "---\nname: step-recipe\ndescription: Has steps\n"
            "steps:\n  plan:\n    tool: run_skill\n---\n"
        )
        info = parse_recipe_metadata(path)
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
        info = parse_recipe_metadata(path)
        assert info.name == "pipeline"
        assert info.description == "A pipeline"

    def test_rejects_empty_file(self, tmp_path: Path) -> None:
        """Empty file raises ValueError."""
        path = tmp_path / "empty.yaml"
        path.write_text("")
        with pytest.raises(ValueError, match="mapping"):
            parse_recipe_metadata(path)

    def test_rejects_non_mapping(self, tmp_path: Path) -> None:
        """File with YAML list raises ValueError."""
        path = tmp_path / "list.yaml"
        path.write_text("- item1\n- item2\n")
        with pytest.raises(ValueError, match="mapping"):
            parse_recipe_metadata(path)

    def test_rejects_missing_name(self, tmp_path: Path) -> None:
        """File without name field raises ValueError."""
        path = tmp_path / "noname.yaml"
        path.write_text("description: No name here\n")
        with pytest.raises(ValueError, match="name"):
            parse_recipe_metadata(path)


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
        with pytest.raises(ValueError, match="not found"):
            _extract_frontmatter(text)


# ---------------------------------------------------------------------------
# TestRecipeVersion: RecipeInfo includes version from autoskillit_version field
# ---------------------------------------------------------------------------


class TestRecipeVersion:
    """RecipeInfo includes version from autoskillit_version field."""

    # SV1+SV2 merged
    @pytest.mark.parametrize("version_val,expected", [(None, None), ("0.2.0", "0.2.0")])
    def test_version_field(self, version_val, expected, tmp_path: Path) -> None:
        path = tmp_path / "recipe.yaml"
        content = "name: my-recipe\ndescription: A recipe\n"
        if version_val is not None:
            content += f'autoskillit_version: "{version_val}"\n'
        path.write_text(content)
        info = parse_recipe_metadata(path)
        assert info.version == expected


# RL-VK1 — AST: recipe_loader must not hard-code the version key string
def test_version_key_not_hardcoded_in_recipe_loader():
    import ast
    import inspect

    import autoskillit.recipe.loader as rl

    source = inspect.getsource(rl)
    tree = ast.parse(source)
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value == "autoskillit_version"
    ]
    assert literals == [], (
        "recipe_loader must import AUTOSKILLIT_VERSION_KEY, not hard-code the string"
    )


# ---------------------------------------------------------------------------
# RL-VER1: all bundled recipes must declare autoskillit_version
# ---------------------------------------------------------------------------
def test_all_bundled_recipes_have_autoskillit_version() -> None:
    """All bundled recipes must declare autoskillit_version to prevent spurious auto-migration."""
    import yaml

    from autoskillit.recipe.io import builtin_recipes_dir

    missing = []
    for recipe_file in builtin_recipes_dir().glob("*.yaml"):
        data = yaml.safe_load(recipe_file.read_text())
        if not data or "autoskillit_version" not in data:
            missing.append(recipe_file.name)
    assert not missing, (
        "Bundled recipes missing 'autoskillit_version' field "
        "(will trigger spurious auto-migration on every load_recipe call):\n"
        + "\n".join(sorted(missing))
    )
