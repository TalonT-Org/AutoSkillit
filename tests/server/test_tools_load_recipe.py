"""Tests for autoskillit server load_recipe and migrate_recipe tools."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core import SkillResult
from autoskillit.core.types import RetryReason
from autoskillit.pipeline.gate import GATED_TOOLS, UNGATED_TOOLS, DefaultGateState
from autoskillit.server.tools_recipe import (
    load_recipe,
    migrate_recipe,
)

# ---------------------------------------------------------------------------
# Minimal valid script YAML used across migration suggestion tests
# ---------------------------------------------------------------------------

_MINIMAL_SCRIPT_YAML = """\
name: test-script
description: Test
summary: test
ingredients:
  task:
    description: What to do
    required: true
steps:
  do-thing:
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
  - "Follow routing rules"
"""


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
    def _ensure_ctx(self, tool_ctx):
        """Ensure server context is initialized (gate open by default)."""

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
    async def test_load_unknown_returns_error(self, tmp_path, monkeypatch):
        """load_recipe returns error JSON for unknown recipe name."""
        monkeypatch.chdir(tmp_path)
        result = json.loads(await load_recipe(name="nonexistent"))
        assert "error" in result
        assert "nonexistent" in result["error"]

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
        assert "content" in result
        assert len(result["content"]) > 0

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
            patch("autoskillit.recipe._api._logger") as mock_logger,
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
    def _setup_ctx(self, tool_ctx):
        """Initialize ToolContext so load_recipe can call _get_config()."""

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
        with patch("autoskillit.recipe._api.load_yaml", side_effect=YAMLError("bad yaml")):
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
        self, tmp_path, monkeypatch, tool_ctx
    ):
        """SUP1: outdated-recipe-version absent when recipe is suppressed; headless not called."""
        from autoskillit.config import MigrationConfig

        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        _write_minimal_script(scripts_dir, "test-script")

        tool_ctx.config = AutomationConfig(migration=MigrationConfig(suppressed=["test-script"]))

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
        self, tmp_path, tool_ctx
    ):
        """SUP4: validate_recipe includes outdated-script-version even when suppressed."""
        from autoskillit.config import MigrationConfig
        from autoskillit.server.tools_recipe import validate_recipe

        script = tmp_path / "test-script.yaml"
        script.write_text(_MINIMAL_SCRIPT_YAML)

        # Even with script suppressed in config, validate_recipe does not filter
        tool_ctx.config = AutomationConfig(migration=MigrationConfig(suppressed=["test-script"]))

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
        from autoskillit.server.helpers import _apply_triage_gate

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
        with patch("autoskillit._llm_triage.triage_staleness", mock_triage):
            # First call: triage_staleness invoked once
            await _apply_triage_gate(copy.deepcopy(result_template), name, recipe_info=recipe_info)

        assert mock_triage.call_count == 1
        assert mock_find.call_count == 0, "find() must not be called when recipe_info is injected"

        cache_path = tmp_path / ".autoskillit" / "temp" / "recipe_staleness_cache.json"
        cached = read_staleness_cache(cache_path, name)
        assert cached is not None
        assert cached.triage_result == "cosmetic"

        with patch("autoskillit._llm_triage.triage_staleness", mock_triage):
            # Second call: must read from cache and skip triage_staleness entirely
            await _apply_triage_gate(copy.deepcopy(result_template), name, recipe_info=recipe_info)

        assert mock_triage.call_count == 1, (
            "triage_staleness must not be called on second invocation"
        )


class TestLoadRecipeReadOnly:
    """P4: load_recipe is strictly read-only — no migration, no contract card generation."""

    @pytest.fixture(autouse=True)
    def _ensure_ctx(self, tool_ctx):
        """Ensure server context is initialized (gate open by default)."""

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


class TestMigrateRecipe:
    """P4: migrate_recipe is a gated tool that runs migration engine and regenerates cards."""

    @pytest.fixture(autouse=True)
    def _open_kitchen(self, tool_ctx):
        """migrate_recipe requires tool activation."""
        tool_ctx.gate = DefaultGateState(enabled=True)

    def _setup_migration_env(
        self,
        tmp_path,
        monkeypatch,
        tool_ctx,
        *,
        suppressed: list[str] | None = None,
    ):
        """Create directory structure, fake migration YAML, and config."""
        import autoskillit
        import autoskillit.migration.loader as ml
        from autoskillit.config import MigrationConfig

        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        recipe_path = recipes_dir / "test-script.yaml"
        recipe_path.write_text(_MINIMAL_SCRIPT_YAML)

        installed_ver = autoskillit.__version__
        fake_mig_dir = tmp_path / "migrations"
        fake_mig_dir.mkdir()
        migration_yaml = (
            f"from_version: '0.0.0'\n"
            f"to_version: '{installed_ver}'\n"
            "description: Upgrade scripts\n"
            "changes:\n"
            "  - id: add-summary-field\n"
            "    description: Scripts now require a summary field\n"
            "    instruction: Add summary field to your script\n"
        )
        (fake_mig_dir / "0.0.0-migration.yaml").write_text(migration_yaml)
        monkeypatch.setattr(ml, "_migrations_dir", lambda: fake_mig_dir)

        tool_ctx.config = AutomationConfig(migration=MigrationConfig(suppressed=suppressed or []))

        temp_mig_dir = tmp_path / ".autoskillit" / "temp" / "migrations"
        temp_mig_dir.mkdir(parents=True)

        migrated_content = _MINIMAL_SCRIPT_YAML + f"autoskillit_version: '{installed_ver}'\n"
        return {
            "recipe_path": recipe_path,
            "temp_mig_dir": temp_mig_dir,
            "migrated_content": migrated_content,
            "installed_ver": installed_ver,
        }

    def test_migrate_recipe_is_in_gated_tools(self):
        """migrate_recipe is a gated tool."""
        assert "migrate_recipe" in GATED_TOOLS

    def test_migrate_recipe_not_in_ungated_tools(self):
        """migrate_recipe is not an ungated tool."""
        assert "migrate_recipe" not in UNGATED_TOOLS

    @pytest.mark.anyio
    async def test_migrate_recipe_requires_gate(self, tool_ctx):
        """migrate_recipe returns gate_error when kitchen is closed."""
        tool_ctx.gate = DefaultGateState(enabled=False)
        result = json.loads(await migrate_recipe(name="test"))
        assert result["success"] is False
        assert result["subtype"] == "gate_error"

    @pytest.mark.anyio
    async def test_migrate_recipe_not_found(self, tmp_path, monkeypatch):
        """migrate_recipe returns error for unknown recipe name."""
        monkeypatch.chdir(tmp_path)
        result = json.loads(await migrate_recipe(name="nonexistent"))
        assert "error" in result
        assert "nonexistent" in result["error"]

    @pytest.mark.anyio
    async def test_migrate_recipe_up_to_date(self, tmp_path, monkeypatch):  # SRV-UPD-1
        """migrate_recipe returns up_to_date when no migrations applicable and contract fresh."""
        monkeypatch.chdir(tmp_path)
        with (
            patch("autoskillit.migration.loader.applicable_migrations", return_value=[]),
            patch("autoskillit.recipe.load_recipe_card", return_value={"skill_hashes": {}}),
            patch("autoskillit.recipe.check_contract_staleness", return_value=[]),
        ):
            result = json.loads(await migrate_recipe(name="implementation"))
        assert result.get("status") == "up_to_date"

    # LR1
    @pytest.mark.anyio
    async def test_auto_migrates_outdated_recipe(self, tmp_path, monkeypatch, tool_ctx):
        """LR1: When recipe version < installed, _run_headless_core is called once."""
        ctx = self._setup_migration_env(tmp_path, monkeypatch, tool_ctx)
        (ctx["temp_mig_dir"] / "test-script.yaml").write_text(ctx["migrated_content"])

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
        with (
            patch("autoskillit.execution.headless.run_headless_core", mock_headless),
            patch("autoskillit.recipe.generate_recipe_card", return_value=None),
        ):
            result = json.loads(await migrate_recipe(name="test-script"))

        mock_headless.assert_awaited_once()
        assert result.get("status") == "migrated"
        assert "contracts_regenerated" in result

    # LR4
    @pytest.mark.anyio
    async def test_clears_failure_record_after_successful_migration(
        self, tmp_path, monkeypatch, tool_ctx
    ):
        """LR4: FailureStore.clear(name) is called when migration succeeds."""
        from autoskillit.migration.store import FailureStore, default_store_path

        ctx = self._setup_migration_env(tmp_path, monkeypatch, tool_ctx)
        (ctx["temp_mig_dir"] / "test-script.yaml").write_text(ctx["migrated_content"])

        store = FailureStore(default_store_path(tmp_path))
        store.record(
            name="test-script",
            file_path=ctx["recipe_path"],
            file_type="recipe",
            error="prior failure",
            retries_attempted=1,
        )
        assert store.has_failure("test-script")

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
        with (
            patch("autoskillit.execution.headless.run_headless_core", mock_headless),
            patch("autoskillit.recipe.contracts.generate_recipe_card", return_value=None),
        ):
            await migrate_recipe(name="test-script")

        fresh_store = FailureStore(default_store_path(tmp_path))
        assert not fresh_store.has_failure("test-script")

    # LR5
    @pytest.mark.anyio
    async def test_records_failure_when_migration_fails(self, tmp_path, monkeypatch, tool_ctx):
        """LR5: When headless returns success=False, failure is recorded to failures.json."""
        from autoskillit.migration.store import FailureStore, default_store_path

        self._setup_migration_env(tmp_path, monkeypatch, tool_ctx)

        mock_headless = AsyncMock(
            return_value=SkillResult(
                success=False,
                result="headless failed",
                session_id="",
                subtype="error",
                is_error=True,
                exit_code=1,
                needs_retry=False,
                retry_reason=RetryReason.NONE,
                stderr="",
            )
        )
        with patch("autoskillit.execution.headless.run_headless_core", mock_headless):
            result = json.loads(await migrate_recipe(name="test-script"))

        assert "error" in result
        store = FailureStore(default_store_path(tmp_path))
        assert store.has_failure("test-script")

    # LR7
    @pytest.mark.anyio
    async def test_suppressed_recipe_not_migrated(self, tmp_path, monkeypatch, tool_ctx):
        """LR7: When name in migration.suppressed, headless is never called."""
        self._setup_migration_env(tmp_path, monkeypatch, tool_ctx, suppressed=["test-script"])

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
            result = json.loads(await migrate_recipe(name="test-script"))

        mock_headless.assert_not_called()
        assert result.get("status") == "up_to_date"

    # LR8
    @pytest.mark.anyio
    async def test_up_to_date_recipe_not_migrated(self, tmp_path, monkeypatch, tool_ctx):
        """LR8: When applicable_migrations returns [], headless is never called."""
        import autoskillit
        import autoskillit.migration.loader as ml
        from autoskillit.config import MigrationConfig

        monkeypatch.chdir(tmp_path)
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        current_ver = autoskillit.__version__
        (recipes_dir / "test-script.yaml").write_text(
            _MINIMAL_SCRIPT_YAML + f"autoskillit_version: '{current_ver}'\n"
        )

        empty_mig_dir = tmp_path / "migrations"
        empty_mig_dir.mkdir()
        monkeypatch.setattr(ml, "_migrations_dir", lambda: empty_mig_dir)
        tool_ctx.config = AutomationConfig(migration=MigrationConfig(suppressed=[]))

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
        with (
            patch("autoskillit.execution.headless.run_headless_core", mock_headless),
            patch("autoskillit.recipe.load_recipe_card", return_value={"skill_hashes": {}}),
            patch("autoskillit.recipe.check_contract_staleness", return_value=[]),
        ):
            result = json.loads(await migrate_recipe(name="test-script"))

        mock_headless.assert_not_called()
        assert result.get("status") == "up_to_date"

    # SRV-NEW-1
    @pytest.mark.anyio
    async def test_migrate_recipe_regenerates_stale_contract(
        self, tmp_path, monkeypatch, tool_ctx
    ):
        """migrate_recipe with version migration also regenerates stale contracts."""
        ctx = self._setup_migration_env(tmp_path, monkeypatch, tool_ctx)
        (ctx["temp_mig_dir"] / "test-script.yaml").write_text(ctx["migrated_content"])

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
        with (
            patch("autoskillit.execution.headless.run_headless_core", mock_headless),
            patch("autoskillit.recipe.load_recipe_card", return_value=None),
            patch("autoskillit.recipe.generate_recipe_card", return_value={}),
        ):
            result = json.loads(await migrate_recipe(name="test-script"))

        assert result.get("status") == "migrated"
        assert result.get("contracts_regenerated") == ["test-script"]


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
    async def test_load_recipe_response_has_diagram_key(self, tmp_path, monkeypatch, tool_ctx):
        """DG-12: load_recipe response always contains a 'diagram' key."""
        self._setup_project_recipe(tmp_path, monkeypatch)
        result = json.loads(await load_recipe(name="my-recipe"))
        assert "diagram" in result

    # DG-13
    @pytest.mark.anyio
    async def test_load_recipe_diagram_none_when_not_generated(
        self, tmp_path, monkeypatch, tool_ctx
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
