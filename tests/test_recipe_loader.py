"""Tests for path-based recipe metadata utilities in recipe_loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe_loader import (
    _extract_frontmatter,
    parse_recipe_metadata,
)


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
        with pytest.raises(ValueError):
            _extract_frontmatter(text)


# ---------------------------------------------------------------------------
# TestRecipeVersion: RecipeInfo includes version from autoskillit_version field
# ---------------------------------------------------------------------------


class TestRecipeVersion:
    """RecipeInfo includes version from autoskillit_version field."""

    # SV1: RecipeInfo.version is None when field absent
    def test_version_none_when_absent(self, tmp_path: Path) -> None:
        """parse_recipe_metadata sets version=None when autoskillit_version is absent."""
        path = tmp_path / "recipe.yaml"
        path.write_text("name: my-recipe\ndescription: A recipe\n")
        info = parse_recipe_metadata(path)
        assert info.version is None

    # SV2: RecipeInfo.version is "0.2.0" when field present
    def test_version_set_when_present(self, tmp_path: Path) -> None:
        """parse_recipe_metadata reads autoskillit_version and stores it as version."""
        path = tmp_path / "recipe.yaml"
        path.write_text('name: my-recipe\ndescription: A recipe\nautoskillit_version: "0.2.0"\n')
        info = parse_recipe_metadata(path)
        assert info.version == "0.2.0"


class TestSyncRemoval:
    def test_no_sync_bundled_recipes_function(self):
        """REQ-SYNC-004: sync_bundled_recipes does not exist in recipe_loader."""
        import autoskillit.recipe_loader as rl

        assert not hasattr(rl, "sync_bundled_recipes")

    def test_no_get_pending_recipe_updates_function(self):
        """REQ-SYNC-004: _get_pending_recipe_updates does not exist in recipe_loader."""
        import autoskillit.recipe_loader as rl

        assert not hasattr(rl, "_get_pending_recipe_updates")

    def test_recipe_loader_has_no_sync_manifest_import(self):
        """REQ-SYNC-005: recipe_loader does not import from sync_manifest."""
        import ast
        from pathlib import Path

        src = Path(__file__).parent.parent / "src" / "autoskillit" / "recipe_loader.py"
        tree = ast.parse(src.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert "sync_manifest" not in node.module


# RL-VK1 — AST: recipe_loader must not hard-code the version key string
def test_version_key_not_hardcoded_in_recipe_loader():
    import ast
    import inspect

    import autoskillit.recipe_loader as rl

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
# RL-PUB1: parse_recipe_metadata exists as a public attribute
# ---------------------------------------------------------------------------
def test_parse_recipe_metadata_is_public() -> None:
    import autoskillit.recipe_loader as rl

    assert hasattr(rl, "parse_recipe_metadata")
    assert callable(rl.parse_recipe_metadata)


# ---------------------------------------------------------------------------
# RL-PUB2: _parse_recipe_metadata no longer exists
# ---------------------------------------------------------------------------
def test_parse_recipe_metadata_private_removed() -> None:
    import autoskillit.recipe_loader as rl

    assert not hasattr(rl, "_parse_recipe_metadata")
