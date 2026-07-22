"""Tests for autoskillit server load_recipe tool."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core import SkillResult
from autoskillit.core.types import RetryReason
from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.server.tools.tools_recipe import load_recipe
from tests.server._helpers import _MINIMAL_SCRIPT_YAML

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _write_minimal_script(scripts_dir: Path, name: str = "test-script") -> Path:
    """Write a minimal valid workflow script with no autoskillit_version field."""
    scripts_dir.mkdir(parents=True, exist_ok=True)
    path = scripts_dir / f"{name}.yaml"
    path.write_text(_MINIMAL_SCRIPT_YAML)
    return path


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
        """load_recipe returns a compact envelope with artifact_path and suggestions."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text("name: test\ndescription: Test recipe\n")
        result = json.loads(await load_recipe(name="test"))
        # Envelope shape: full content is NOT in the response — it is
        # retrieved on demand via get_recipe_section(section="content").
        assert "content" not in result
        assert "artifact_path" in result
        assert "sha256" in result
        assert "pull_tool" in result
        assert result["pull_tool"] == "get_recipe_section"
        assert "suggestions" in result
        # Verify the persisted artifact carries the full recipe content
        artifact = Path(result["artifact_path"]).read_text(encoding="utf-8")
        payload = json.loads(artifact)
        assert "name: test" in payload["content"]
        assert "description: Test recipe" in payload["content"]

    # SS3
    @pytest.mark.anyio
    async def test_load_unknown_returns_error(self, tmp_path, monkeypatch):
        """load_recipe returns error JSON for unknown recipe name."""
        monkeypatch.chdir(tmp_path)
        result = json.loads(await load_recipe(name="nonexistent"))
        assert "error" in result
        assert "nonexistent" in result["error"]

    # SS7
    @pytest.mark.anyio
    async def test_load_returns_json_with_suggestions(self, tmp_path, monkeypatch):
        """load_recipe envelope always has 'suggestions' list with semantic findings."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text(
            "name: test\ndescription: Test\nkitchen_rules:\n  - test\n"
            "steps:\n  do:\n    tool: test_check\n    model: sonnet\n"
            "    on_success: done\n  done:\n    action: stop\n    message: Done\n"
        )
        result = json.loads(await load_recipe(name="test"))
        assert "content" not in result
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
        # New envelope: full content is in artifact_path, not inline
        assert "content" not in result
        assert "artifact_path" in result
        artifact = Path(result["artifact_path"]).read_text(encoding="utf-8")
        payload = json.loads(artifact)
        assert "content" in payload
        assert len(payload["content"]) > 0

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

        assert "artifact_path" in result, (
            "load_recipe must persist an artifact and return its path even on parse failure"
        )
        mock_logger.warning.assert_called_once()
        assert any(s.get("rule") == "validation-error" for s in result["suggestions"]), (
            "Unexpected exception must appear as a validation-error finding in suggestions"
        )
        findings = [s for s in result["suggestions"] if s.get("rule") == "validation-error"]
        assert findings, "Expected at least one validation-error finding"
        assert findings[0]["message"] == "Invalid recipe structure: injected crash"


class TestContractMigrationAdapterValidate:
    """P7-2: ContractMigrationAdapter.validate uses _load_yaml, not yaml.safe_load."""

    def test_valid_contract_returns_true(self, tmp_path: Path) -> None:
        from autoskillit.migration.engine import ContractMigrationAdapter

        f = tmp_path / "contract.yaml"
        f.write_text("skill_hashes:\n  my-skill: abc123\n")
        adapter = ContractMigrationAdapter()
        ok, msg = adapter.validate(f)
        assert ok is True
        assert msg == ""

    def test_missing_skill_hashes_returns_false(self, tmp_path: Path) -> None:
        from autoskillit.migration.engine import ContractMigrationAdapter

        f = tmp_path / "contract.yaml"
        f.write_text("other_field: value\n")
        adapter = ContractMigrationAdapter()
        ok, msg = adapter.validate(f)
        assert ok is False
        assert "skill_hashes" in msg

    def test_invalid_yaml_returns_false(self, tmp_path: Path) -> None:
        from autoskillit.migration.engine import ContractMigrationAdapter

        f = tmp_path / "contract.yaml"
        f.write_bytes(b":\tbad: yaml: [unclosed\n")
        adapter = ContractMigrationAdapter()
        ok, msg = adapter.validate(f)
        assert ok is False
        assert msg != ""

    def test_missing_file_returns_false(self, tmp_path: Path) -> None:
        from autoskillit.migration.engine import ContractMigrationAdapter

        adapter = ContractMigrationAdapter()
        ok, msg = adapter.validate(tmp_path / "nonexistent.yaml")
        assert ok is False
        assert msg != ""


class TestLoadRecipeExceptionHandling:
    """CC-1: Outer except in load_recipe must catch anticipated exceptions only."""

    @pytest.fixture(autouse=True)
    def _setup_ctx(self, tool_ctx_kitchen_open):
        """Initialize ToolContext with gate open so load_recipe can call _get_config()."""

    @pytest.mark.anyio
    async def test_yaml_error_surfaces_as_suggestion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """yaml.YAMLError is caught and returned as an error suggestion."""
        from autoskillit.core.io import YAMLError

        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text("name: test\n")
        with patch("autoskillit.recipe._api._load_recipe_dict", side_effect=YAMLError("bad yaml")):
            result = json.loads(await load_recipe(name="test"))
        assert "error" not in result
        assert any(
            s.get("rule") == "validation-error" and s.get("severity") == "error"
            for s in result["suggestions"]
        )

    @pytest.mark.anyio
    async def test_value_error_surfaces_as_suggestion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ValueError (malformed recipe structure) is caught and returned as error suggestion."""
        from autoskillit.core.types import RecipeSource
        from autoskillit.recipe.schema import RecipeInfo

        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        recipe_path = recipes_dir / "test.yaml"
        recipe_path.write_text(
            "name: test\ndescription: Test\nsteps:\n  done:\n    action: stop\n    message: Done\n"
        )
        fake_match = RecipeInfo(
            name="test",
            description="Test",
            source=RecipeSource.PROJECT,
            path=recipe_path,
        )
        with (
            patch("autoskillit.recipe.find_recipe_by_name", return_value=fake_match),
            patch(
                "autoskillit.recipe._api._parse_recipe", side_effect=ValueError("bad structure")
            ),
        ):
            result = json.loads(await load_recipe(name="test"))
        assert "error" not in result
        assert any(
            s.get("rule") == "validation-error" and s.get("severity") == "error"
            for s in result["suggestions"]
        )

    @pytest.mark.anyio
    async def test_file_not_found_surfaces_as_suggestion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FileNotFoundError is caught and returned as an error suggestion."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text(
            "name: test\ndescription: Test\nsteps:\n  done:\n    action: stop\n    message: Done\n"
        )
        with patch(
            "autoskillit.recipe._api.load_recipe_card",
            side_effect=FileNotFoundError("missing"),
        ):
            result = json.loads(await load_recipe(name="test"))
        assert "error" not in result
        assert any(
            s.get("rule") == "validation-error" and s.get("severity") == "error"
            for s in result["suggestions"]
        )

    @pytest.mark.anyio
    async def test_unexpected_exception_returns_structured_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unexpected exceptions are caught by the handler-level exception boundary."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text(
            "name: test\ndescription: Test\nsteps:\n  done:\n    action: stop\n    message: Done\n"
        )
        with patch(
            "autoskillit.recipe._api.run_semantic_rules",
            side_effect=AttributeError("programming error"),
        ):
            result = json.loads(await load_recipe(name="test"))
        assert result["success"] is False
        assert "error" in result
        assert "programming error" in result["error"]


class TestMigrationSuppression:
    """SUP1, SUP4: load_recipe respects migration.suppressed config."""

    # SUP1
    @pytest.mark.anyio
    async def test_outdated_version_not_in_suggestions_when_suppressed(
        self, tmp_path, monkeypatch, tool_ctx_kitchen_open
    ):
        """SUP1: outdated-recipe-version absent when recipe is suppressed; headless not called."""
        from autoskillit.config import MigrationConfig

        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        _write_minimal_script(scripts_dir, "test-script")

        tool_ctx_kitchen_open.config = AutomationConfig(
            migration=MigrationConfig(suppressed=["test-script"])
        )

        mock_headless = AsyncMock(
            return_value=SkillResult(
                success=True,
                result="ok",
                session_id="",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
            )
        )
        with patch("autoskillit.execution.headless.run_headless_core", mock_headless):
            result = json.loads(await load_recipe(name="test-script"))

        assert "suggestions" in result
        rules = [s["rule"] for s in result["suggestions"]]
        assert "outdated-recipe-version" not in rules
        mock_headless.assert_not_called()

    # SUP4
    @pytest.mark.anyio
    async def test_validate_always_includes_outdated_version_regardless_of_suppression(
        self, tmp_path, tool_ctx_kitchen_open
    ):
        """SUP4: validate_recipe includes outdated-script-version even when suppressed."""
        from autoskillit.config import MigrationConfig
        from autoskillit.server.tools.tools_recipe import validate_recipe

        script = tmp_path / "test-script.yaml"
        script.write_text(_MINIMAL_SCRIPT_YAML + 'autoskillit_version: "0.0.1"\n')

        # Even with script suppressed in config, validate_recipe does not filter
        tool_ctx_kitchen_open.config = AutomationConfig(
            migration=MigrationConfig(suppressed=["test-script"])
        )

        result = json.loads(await validate_recipe(script_path=str(script)))
        assert "findings" in result
        rules = [s["rule"] for s in result["findings"]]
        assert "outdated-recipe-version" in rules


class TestApplyTriageGate:
    """T3: _apply_triage_gate caches triage result and skips on second call."""

    @pytest.fixture(autouse=True)
    def _close_kitchen(self, tool_ctx):
        tool_ctx.gate = DefaultGateState(enabled=False)

    @pytest.mark.anyio
    async def test_apply_triage_gate_second_call_skips_triage(
        self, tmp_path, monkeypatch, tool_ctx
    ):
        """Second _apply_triage_gate call reads from cache; triage_staleness not re-invoked."""
        import copy

        from autoskillit.recipe.staleness_cache import read_staleness_cache
        from autoskillit.server._misc import _apply_triage_gate

        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        recipe_yaml = (
            "name: triage-test\ndescription: T\n"
            "steps:\n  done:\n    action: stop\n    message: Done\n"
        )
        recipe_path = recipes_dir / "triage-test.yaml"
        recipe_path.write_text(recipe_yaml)

        name = "triage-test"
        result_template = {
            "content": recipe_yaml,
            "suggestions": [
                {
                    "rule": "stale-contract",
                    "reason": "hash_mismatch",
                    "skill": "investigate",
                    "stored_value": "sha256:old",
                    "current_value": "sha256:new",
                    "message": "investigate SKILL.md changed",
                    "severity": "info",
                }
            ],
            "valid": True,
        }

        # Get real recipe_info before mocking find
        recipe_info = tool_ctx.recipes.find(name, Path.cwd())
        assert recipe_info is not None

        # Mock _ctx.recipes.find to verify it is NOT called when recipe_info is injected
        mock_find = AsyncMock(return_value=recipe_info)
        monkeypatch.setattr(tool_ctx.recipes, "find", mock_find)

        mock_triage = AsyncMock(
            return_value=[{"meaningful": False, "summary": "ok", "skill": "investigate"}]
        )
        with patch("autoskillit.server._misc.triage_staleness", mock_triage):
            # First call: triage_staleness invoked once
            await _apply_triage_gate(copy.deepcopy(result_template), name, recipe_info=recipe_info)

        assert mock_triage.call_count == 1
        assert mock_find.call_count == 0, "find() must not be called when recipe_info is injected"

        cache_path = tmp_path / ".autoskillit" / "temp" / "recipe_staleness_cache.json"
        cached = read_staleness_cache(cache_path, name)
        assert cached is not None
        assert cached.triage_result == "cosmetic"

        with patch("autoskillit.server._misc.triage_staleness", mock_triage):
            # Second call: must read from cache and skip triage_staleness entirely
            await _apply_triage_gate(copy.deepcopy(result_template), name, recipe_info=recipe_info)

        assert mock_triage.call_count == 1, (
            "triage_staleness must not be called on second invocation"
        )


class TestLoadRecipeReadOnly:
    """P4: load_recipe is strictly read-only — no migration, no contract card generation."""

    @pytest.fixture(autouse=True)
    def _ensure_ctx(self, tool_ctx_kitchen_open):
        """Ensure server context is initialized with gate open."""

    @pytest.mark.anyio
    async def test_load_recipe_does_not_call_migration_engine(self, tmp_path, monkeypatch):
        """load_recipe must not trigger headless migration even when migrations are applicable."""
        monkeypatch.chdir(tmp_path)
        with (
            patch("autoskillit.migration.loader.applicable_migrations", return_value=["v0.1.0"]),
            patch("autoskillit.execution.headless.run_headless_core") as mock_headless,
            patch("autoskillit.recipe.contracts.generate_recipe_card") as mock_gen,
        ):
            result = json.loads(await load_recipe(name="implementation"))
        assert "error" not in result
        mock_headless.assert_not_called()
        mock_gen.assert_not_called()

    @pytest.mark.anyio
    async def test_load_recipe_does_not_auto_generate_contract_card(self, tmp_path, monkeypatch):
        """load_recipe must not call generate_recipe_card even when no card exists."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text(
            "name: test\ndescription: Test\nsteps:\n  done:\n    action: stop\n    message: Done\n"
        )
        with patch("autoskillit.recipe.contracts.generate_recipe_card") as mock_gen:
            await load_recipe(name="test")
        mock_gen.assert_not_called()


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
        """DG-12: load_recipe envelope carries diagram field when present."""
        self._setup_project_recipe(tmp_path, monkeypatch)
        result = json.loads(await load_recipe(name="my-recipe"))
        # New envelope: diagram is forwarded only when present and not None;
        # canonical path is get_recipe_section(section="diagram"). When no
        # diagram exists, the key is absent.
        # Verify either the inline diagram key is present OR the artifact_path
        # exposes a 'diagram' field via the persisted artifact.
        assert "artifact_path" in result
        if "diagram" in result:
            assert result["diagram"] is None or isinstance(result["diagram"], str)
        else:
            artifact = Path(result["artifact_path"]).read_text(encoding="utf-8")
            payload = json.loads(artifact)
            assert "diagram" in payload

    # DG-13
    @pytest.mark.anyio
    async def test_load_recipe_diagram_none_when_not_generated(
        self, tmp_path, monkeypatch, tool_ctx_kitchen_open
    ):
        """DG-13: persisted artifact's diagram is None when no diagram file exists."""
        self._setup_project_recipe(tmp_path, monkeypatch)
        result = json.loads(await load_recipe(name="my-recipe"))
        # Envelope carries diagram only when explicitly present and not None.
        # The canonical retrieval path is get_recipe_section(section="diagram").
        assert "diagram" not in result or result.get("diagram") is None
        artifact = Path(result["artifact_path"]).read_text(encoding="utf-8")
        payload = json.loads(artifact)
        assert payload.get("diagram") is None


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
        """load_recipe(name=X, ingredients_only=True) returns a minimal envelope."""
        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "test.yaml").write_text(
            "name: test\ndescription: Test recipe\nsteps:\n"
            "  done:\n    action: stop\n    message: Done\n"
        )
        result = json.loads(await load_recipe(name="test", ingredients_only=True))
        # New envelope shape — even when ingredients_only, the envelope fields
        # are present (no full content), but step_flow_skeleton is empty.
        assert "content" not in result
        # Orchestration fields are part of the envelope; when ingredients_only=True,
        # they are stripped per the strip_ingredients_only_keys policy before the
        # envelope is built, so they surface here as falsy (present but None).
        assert not result.get("orchestration_rules")
        assert not result.get("stop_step_semantics")
        assert result.get("step_flow_skeleton") == []
        assert "suggestions" in result


class TestLoadRecipeAuthorityClobber:
    """load_recipe must emit an authority-clobber warning when a server-authoritative
    key is overridden by the caller. Enforcement (config-layer wins) already works;
    this test verifies the *feedback* contract."""

    @pytest.mark.anyio
    async def test_load_recipe_emits_authority_warning(self, tmp_path, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        from tests.server.conftest import _make_mock_ctx

        monkeypatch.chdir(tmp_path)
        mock_ctx = _make_mock_ctx()
        mock_ctx.enable_components = AsyncMock()
        mock_ctx.recipes = MagicMock()
        mock_recipe_obj = MagicMock()
        mock_recipe_obj.steps = {"do": MagicMock()}
        mock_recipe_obj.ingredients = {"base_branch": MagicMock()}
        mock_ctx.recipes.load.return_value = mock_recipe_obj
        mock_ctx.recipes.load_and_validate.return_value = {
            "content": "name: demo\nsteps:\n  do:\n    tool: run_cmd\n",
            "valid": True,
            "suggestions": [],
            "diagram": None,
            "ingredients_table": "--- TABLE ---",
        }
        mock_ctx.recipes.find.return_value = None
        mock_ctx.config.migration.suppressed = []
        mock_ctx.kitchen_id = "test-kitchen"
        mock_ctx.config.linux_tracing.log_dir = ""
        # New envelope: load_recipe persists the full recipe payload to
        # tool_ctx.temp_dir/responses/load_recipe/. Provide a real temp_dir
        # so artifact_dir.mkdir succeeds under the mock.
        mock_ctx.temp_dir = tmp_path

        with patch(
            "autoskillit.server.tools.tools_recipe._get_ctx_or_none",
            return_value=mock_ctx,
        ):
            with patch(
                "autoskillit.server.tools.tools_recipe._require_enabled",
                return_value=None,
            ):
                with patch("autoskillit.server.logger"):
                    with patch(
                        "autoskillit.server.tools.tools_recipe.resolve_ingredient_defaults",
                        return_value={
                            "base_branch": "develop",
                            "is_fleet_dispatch": "false",
                            "dispatch_id": "",
                        },
                    ):
                        from autoskillit.server.tools.tools_recipe import load_recipe

                        result_str = await load_recipe(
                            name="demo",
                            overrides={"base_branch": "custom"},
                        )

        parsed = json.loads(result_str)
        # New envelope: warnings live on the persisted artifact (full payload),
        # not on the compact envelope. Read the artifact to verify the
        # authority-clobber warning is generated and persisted.
        artifact_path = parsed.get("artifact_path")
        assert artifact_path, f"load_recipe envelope missing artifact_path; got {parsed}"
        artifact = Path(artifact_path).read_text(encoding="utf-8")
        persisted_payload = json.loads(artifact)
        warnings = persisted_payload.get("warnings") or []
        matching = [w for w in warnings if "base_branch" in w]
        assert matching, (
            f"load_recipe must emit a warning naming base_branch; got warnings={warnings}"
        )
        server_value_match = [w for w in warnings if "server value 'develop'" in w]
        assert server_value_match, (
            "Authority-clobber warning must confirm config value won — "
            f"expected \"server value 'develop'\" in warning text; got warnings={warnings}"
        )
        caller_value_absent = [w for w in warnings if "server value 'custom'" in w]
        assert not caller_value_absent, (
            "Authority-clobber warning must NOT report the caller override as the server value — "
            f"got warnings={warnings}"
        )


# 1i: load_recipe tool surfaces validation failure
class TestLoadRecipeSurfacesValidationFailure:
    """When load_and_validate returns valid=False, the load_recipe tool must include
    a field indicating the recipe failed validation.
    """

    @pytest.fixture(autouse=True)
    def _ensure_ctx(self, tool_ctx_kitchen_open):
        """Ensure server context is initialized with gate open."""

    @pytest.mark.anyio
    async def test_load_recipe_surfaces_validation_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When load_and_validate returns valid=False, the response must include
        a validation_failed indicator so callers know the recipe is invalid.
        """
        from autoskillit.recipe._api_cache import _LOAD_CACHE

        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "no-steps.yaml").write_text(
            "name: no-steps\ndescription: Missing steps\nkitchen_rules:\n  - 'rule'\n"
        )

        _LOAD_CACHE.clear()

        result = json.loads(await load_recipe(name="no-steps"))
        # New envelope: validation_failed/valid/errors live in the artifact
        # (the compact envelope strips these). Read the artifact to confirm
        # the failure was surfaced for callers who fetch the persisted payload.
        assert result.get("success") is True
        assert "artifact_path" in result
        artifact = Path(result["artifact_path"]).read_text(encoding="utf-8")
        persisted = json.loads(artifact)
        assert persisted.get("valid") is False
        assert persisted.get("validation_failed") is True, (
            f"Expected validation_failed=True; got keys: {list(persisted.keys())}"
        )
        assert "errors" in persisted
        assert len(persisted["errors"]) > 0


class TestLoadRecipeFailClosed:
    """Fail-closed validation for empty and missing content."""

    @pytest.fixture(autouse=True)
    def _ensure_ctx(self, tool_ctx_kitchen_open):
        self.ctx = tool_ctx_kitchen_open

    @pytest.mark.anyio
    async def test_load_recipe_fail_closed_empty_content(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        from autoskillit.recipe._api_cache import _LOAD_CACHE

        _LOAD_CACHE.clear()
        test_result = {"valid": True, "content": "", "dispatch_feasible": True}
        monkeypatch.setattr(self.ctx.recipes, "load_and_validate", lambda *a, **kw: test_result)
        monkeypatch.setattr(self.ctx.recipes, "find", lambda *a, **kw: None)
        with patch(
            "autoskillit.server.tools.tools_recipe._apply_triage_gate",
            new=AsyncMock(return_value=test_result),
        ):
            raw = await load_recipe(name="test-recipe")
        parsed = json.loads(raw)
        assert parsed["success"] is False
        assert "content" in parsed["error"].lower()

    @pytest.mark.anyio
    async def test_load_recipe_fail_closed_missing_content(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        from autoskillit.recipe._api_cache import _LOAD_CACHE

        _LOAD_CACHE.clear()
        test_result = {"valid": True, "dispatch_feasible": True}
        monkeypatch.setattr(self.ctx.recipes, "load_and_validate", lambda *a, **kw: test_result)
        monkeypatch.setattr(self.ctx.recipes, "find", lambda *a, **kw: None)
        with patch(
            "autoskillit.server.tools.tools_recipe._apply_triage_gate",
            new=AsyncMock(return_value=test_result),
        ):
            raw = await load_recipe(name="test-recipe")
        parsed = json.loads(raw)
        assert parsed["success"] is False
        assert "content" in parsed["error"]

    @pytest.mark.anyio
    async def test_load_recipe_blocks_on_dispatch_infeasible(self, monkeypatch, tmp_path):
        """load_recipe must hard-block when dispatch_feasible=False — no recipe
        content is delivered to the caller when the pipeline is infeasible."""
        monkeypatch.chdir(tmp_path)
        from autoskillit.recipe._api_cache import _LOAD_CACHE

        _LOAD_CACHE.clear()
        test_result = {
            "valid": True,
            "content": "name: blocked-recipe\nsteps:\n  build:\n    cmd: task build\n",
            "dispatch_feasible": False,
            "infeasible_steps": ["gate_backend_write"],
        }
        monkeypatch.setattr(self.ctx.recipes, "load_and_validate", lambda *a, **kw: test_result)
        monkeypatch.setattr(self.ctx.recipes, "find", lambda *a, **kw: None)
        with patch(
            "autoskillit.server.tools.tools_recipe._apply_triage_gate",
            new=AsyncMock(return_value=test_result),
        ):
            raw = await load_recipe(name="blocked-recipe")
        parsed = json.loads(raw)
        assert parsed["success"] is False
        assert parsed.get("dispatch_infeasible") is True
        assert "gate_backend_write" in parsed.get("infeasible_steps", [])
        assert "content" not in parsed, (
            "load_recipe must NOT deliver recipe content when dispatch is infeasible; "
            f"got keys: {list(parsed.keys())}"
        )
