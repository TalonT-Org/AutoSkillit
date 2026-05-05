"""Tests for autoskillit server migrate_recipe tool."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core import SkillResult
from autoskillit.core.types import RetryReason
from autoskillit.pipeline.gate import GATED_TOOLS, UNGATED_TOOLS, DefaultGateState
from autoskillit.server.tools.tools_recipe import migrate_recipe
from tests.server._helpers import _MINIMAL_SCRIPT_YAML

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


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
