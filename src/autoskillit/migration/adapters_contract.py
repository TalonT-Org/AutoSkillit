"""Contract migration adapter — deterministic contract-card regeneration."""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import get_logger, load_yaml
from autoskillit.migration.engine import (
    DeterministicMigrationAdapter,
    MigrationFile,
    MigrationResult,
)

logger = get_logger(__name__)


class ContractMigrationAdapter(DeterministicMigrationAdapter):
    file_type = "contract"

    def discover(self, project_dir: Path) -> list[MigrationFile]:
        contracts_dir = project_dir / ".autoskillit" / "recipes" / "contracts"
        if not contracts_dir.exists():
            return []
        files = []
        for p in sorted(contracts_dir.glob("*.yaml")):
            files.append(
                MigrationFile(
                    name=p.stem,
                    path=p,
                    file_type=self.file_type,
                    current_version=None,  # version tracked via staleness, not semver
                )
            )
        return files

    def needs_migration(self, file: MigrationFile) -> bool:
        from autoskillit.recipe import check_contract_staleness, load_recipe_card  # noqa: PLC0415

        recipes_dir = file.path.parent.parent
        contract = load_recipe_card(file.name, recipes_dir)
        if contract is None:
            return True
        return bool(check_contract_staleness(contract))

    async def migrate(
        self,
        file: MigrationFile,
        *,
        temp_dir: Path,
    ) -> MigrationResult:
        from autoskillit.recipe import generate_recipe_card  # noqa: PLC0415

        recipes_dir = file.path.parent.parent
        recipe_path = recipes_dir / f"{file.name}.yaml"
        if not recipe_path.exists():
            return MigrationResult(
                success=False,
                name=file.name,
                error=f"Source recipe '{file.name}.yaml' not found",
            )
        try:
            _ = generate_recipe_card(recipe_path, recipes_dir)
            return MigrationResult(success=True, name=file.name)
        except Exception as exc:
            logger.warning("Contract card generation failed", name=file.name, error=str(exc))
            return MigrationResult(success=False, name=file.name, error=str(exc))

    def validate(self, path: Path) -> tuple[bool, str]:
        try:
            data = load_yaml(path)
            if not isinstance(data, dict) or "skill_hashes" not in data:
                return False, "missing skill_hashes field"
            return True, ""
        except Exception as exc:
            logger.warning("Contract file validation failed", path=str(path), error=str(exc))
            return False, str(exc)
