"""Contract-migration adapter validation and load_recipe validation failure
surface tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.core import SkillResolver
from autoskillit.server.tools.tools_recipe import load_recipe

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.fixture(autouse=True)
def _default_recipe_names_do_not_resolve_as_skills(tool_ctx) -> None:
    tool_ctx.skill_resolver = MagicMock(spec=SkillResolver)
    tool_ctx.skill_resolver.resolve_effective.return_value = None


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
        assert result.get("valid") is False
        assert result.get("validation_failed") is True, (
            f"Expected validation_failed=True in response; got keys: {list(result.keys())}"
        )
        assert "errors" in result
        assert len(result["errors"]) > 0
