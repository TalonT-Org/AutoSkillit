"""Migration-suppression and triage-gate tests for load_recipe (SUP1 / SUP4 / T3)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core import SkillResolver, SkillResult
from autoskillit.core.types import RetryReason
from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.server.tools.tools_recipe import load_recipe
from tests.server._helpers import _MINIMAL_SCRIPT_YAML

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.fixture(autouse=True)
def _default_recipe_names_do_not_resolve_as_skills(tool_ctx) -> None:
    tool_ctx.skill_resolver = MagicMock(spec=SkillResolver)
    tool_ctx.skill_resolver.resolve_effective.return_value = None


def _write_minimal_script(scripts_dir: Path, name: str = "test-script") -> Path:
    """Write a minimal valid workflow script with no autoskillit_version field."""
    scripts_dir.mkdir(parents=True, exist_ok=True)
    path = scripts_dir / f"{name}.yaml"
    path.write_text(_MINIMAL_SCRIPT_YAML)
    return path


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
