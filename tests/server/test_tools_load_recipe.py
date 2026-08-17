"""Tests for autoskillit server load_recipe tool (kept file).

Carries the core load_recipe tool tests (skill-name rejection, kitchen-gated
loading, diagram response, ingredients_only, no-context handling) plus the
shared 4-line autouse fixture. Class-specific behaviors live in the
per-concern files:
- test_load_recipe_contract_validation.py — TestContractMigrationAdapterValidate,
  TestLoadRecipeSurfacesValidationFailure
- test_load_recipe_exception_handling.py — TestLoadRecipeExceptionHandling,
  TestLoadRecipeFailClosed
- test_load_recipe_migration.py — TestMigrationSuppression, TestApplyTriageGate
- test_load_recipe_authority.py — TestLoadRecipeReadOnly,
  TestLoadRecipeAuthorityClobber
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.core import SkillResolver
from autoskillit.server.tools.tools_recipe import load_recipe
from tests.server._helpers import _resolve_recipe_section

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.fixture(autouse=True)
def _default_recipe_names_do_not_resolve_as_skills(tool_ctx) -> None:
    tool_ctx.skill_resolver = MagicMock(spec=SkillResolver)
    tool_ctx.skill_resolver.resolve_effective.return_value = None


@pytest.mark.anyio
@pytest.mark.parametrize("recipe_present", [False, True], ids=["skill-only", "ambiguous"])
async def test_load_recipe_rejects_skill_names_before_recipe_work(
    tmp_path, recipe_present: bool
) -> None:
    from tests.server.conftest import _make_mock_ctx

    mock_ctx = _make_mock_ctx()
    mock_ctx.recipes = MagicMock()
    mock_ctx.recipes.find.return_value = object() if recipe_present else None
    mock_ctx.skill_resolver.resolve_effective.return_value = object()

    with (
        patch(
            "autoskillit.server.tools.tools_recipe._get_ctx_or_none",
            return_value=mock_ctx,
        ),
        patch(
            "autoskillit.server.tools.tools_recipe._require_enabled",
            return_value=None,
        ),
    ):
        result = json.loads(await load_recipe(name="shared-name"))

    assert result["success"] is False
    assert "skill" in result["error"].lower()
    if recipe_present:
        assert "ambiguous" in result["error"].lower()
    mock_ctx.recipes.load.assert_not_called()
    mock_ctx.recipes.load_and_validate.assert_not_called()


# ---------------------------------------------------------------------------
# Minimal recipe YAML used for diagram tests
# ---------------------------------------------------------------------------

_MINIMAL_RECIPE_FOR_DIAGRAM = """\
name: my-recipe
description: Test recipe for diagram tests
summary: step1 -> done
ingredients:
  task:
    description: What to do
    required: true
steps:
  step1:
    tool: run_skill
    with:
      skill_command: "/autoskillit:investigate ${{ inputs.task }}"
      cwd: "."
    on_success: done
    on_failure: escalate
  done:
    action: stop
    message: "Done."
  escalate:
    action: stop
    message: "Failed."
kitchen_rules:
  - "Use AutoSkillit tools only"
"""


class TestLoadRecipeTools:
    """Tests for kitchen-gated load_recipe tool."""

    @pytest.fixture(autouse=True)
    def _ensure_ctx(self, tool_ctx_kitchen_open):
        """Ensure server context is initialized with gate open."""

    # SS2
    @pytest.mark.anyio
    async def test_load_returns_json_with_content(self, tmp_path, monkeypatch):
        """load_recipe returns JSON with content and suggestions."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text("name: test\ndescription: Test recipe\n")
        result = json.loads(await load_recipe(name="test"))
        assert "content" in result
        assert "suggestions" in result
        assert "name: test" in result["content"]
        assert "description: Test recipe" in result["content"]

    # SS3
    @pytest.mark.anyio
    async def test_load_unknown_preserves_active_execution(
        self,
        tmp_path,
        monkeypatch,
        tool_ctx_kitchen_open,
    ):
        """load_recipe returns error JSON for unknown recipe name."""
        monkeypatch.chdir(tmp_path)
        previous = MagicMock()
        previous.snapshot.execution_id = "previous-execution"
        tool_ctx_kitchen_open.active_recipe_execution = previous

        result = json.loads(await load_recipe(name="nonexistent"))

        assert "error" in result
        assert "nonexistent" in result["error"]
        assert tool_ctx_kitchen_open.active_recipe_execution is previous

    # SS7
    @pytest.mark.anyio
    async def test_load_returns_json_with_suggestions(self, tmp_path, monkeypatch):
        """load_recipe response always has 'content' and 'suggestions' keys."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text(
            "name: test\ndescription: Test\nkitchen_rules:\n  - test\n"
            "steps:\n  do:\n    tool: test_check\n    model: sonnet\n"
            "    on_success: done\n  done:\n    action: stop\n    message: Done\n"
        )
        result = json.loads(await load_recipe(name="test"))
        assert "content" in result
        assert "suggestions" in result
        assert isinstance(result["suggestions"], list)
        assert any(s["rule"] == "model-on-non-skill-step" for s in result["suggestions"])

    # SS9
    @pytest.mark.anyio
    async def test_load_recipe_mcp_returns_builtin_recipe(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_recipe MCP finds bundled recipes when no project .autoskillit/recipes/ dir."""
        monkeypatch.chdir(tmp_path)
        result = json.loads(await load_recipe(name="implementation"))
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        assert await _resolve_recipe_section(result)

    @pytest.mark.anyio
    async def test_load_recipe_parse_failure_is_logged_and_surfaced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_recipe emits a warning log and surfaces a validation-error finding."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        # Recipe must have 'steps' so the run_semantic_rules code path is reached
        (recipes_dir / "test.yaml").write_text(
            "name: test\ndescription: Test\nsteps:\n  done:\n    action: stop\n    message: Done\n"
        )

        with (
            patch(
                "autoskillit.recipe._api.run_semantic_rules",
                side_effect=ValueError("injected crash"),
            ),
            patch("autoskillit.recipe._api.logger") as mock_logger,
        ):
            result = json.loads(await load_recipe(name="test"))

        assert "content" in result, "load_recipe must be non-blocking even on parse failure"
        mock_logger.warning.assert_called_once()
        assert any(s.get("rule") == "validation-error" for s in result["suggestions"]), (
            "Unexpected exception must appear as a validation-error finding in suggestions"
        )
        findings = [s for s in result["suggestions"] if s.get("rule") == "validation-error"]
        assert findings, "Expected at least one validation-error finding"
        assert findings[0]["message"] == "Invalid recipe structure: injected crash"


class TestLoadRecipeDiagram:
    """Tests for diagram field in load_recipe responses (DG-12 through DG-15)."""

    def _setup_project_recipe(self, tmp_path: Path, monkeypatch) -> Path:
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        recipe_path = recipes_dir / "my-recipe.yaml"
        recipe_path.write_text(_MINIMAL_RECIPE_FOR_DIAGRAM)
        return recipes_dir

    # DG-12
    @pytest.mark.anyio
    async def test_load_recipe_response_has_diagram_key(
        self, tmp_path, monkeypatch, tool_ctx_kitchen_open
    ):
        """DG-12: load_recipe response always contains a 'diagram' key."""
        self._setup_project_recipe(tmp_path, monkeypatch)
        result = json.loads(await load_recipe(name="my-recipe"))
        assert "diagram" in result

    # DG-13
    @pytest.mark.anyio
    async def test_load_recipe_diagram_none_when_not_generated(
        self, tmp_path, monkeypatch, tool_ctx_kitchen_open
    ):
        """DG-13: diagram is None when no diagram file exists."""
        self._setup_project_recipe(tmp_path, monkeypatch)
        result = json.loads(await load_recipe(name="my-recipe"))
        assert result["diagram"] is None


# ---------------------------------------------------------------------------
# P5F2: Accessor pattern tests
# ---------------------------------------------------------------------------


# P5F2-T2
@pytest.mark.anyio
async def test_load_recipe_no_ctx_returns_error(monkeypatch):
    """load_recipe returns error JSON when server is uninitialized."""
    import autoskillit.server._state as _state_mod

    monkeypatch.setattr(_state_mod, "_ctx", None)
    result = json.loads(await load_recipe(name="anything"))
    assert "error" in result


# ---------------------------------------------------------------------------
# ingredients_only parameter
# ---------------------------------------------------------------------------


class TestLoadRecipeIngredientsOnly:
    """load_recipe(ingredients_only=True) strips content, preserves metadata."""

    @pytest.fixture(autouse=True)
    def _ensure_ctx(self, tool_ctx_kitchen_open):
        """Ensure server context is initialized with gate open."""

    @pytest.mark.anyio
    async def test_load_recipe_ingredients_only_strips_content(self, tmp_path, monkeypatch):
        """load_recipe(name=X, ingredients_only=True) must omit content from result."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text(
            "name: test\ndescription: Test recipe\nsteps:\n"
            "  done:\n    action: stop\n    message: Done\n"
        )
        result = json.loads(await load_recipe(name="test", ingredients_only=True))
        assert "content" not in result
        assert "orchestration_rules" not in result
        assert "stop_step_semantics" not in result
        assert "suggestions" in result
