"""Recipe migration adapter — headless LLM-driven recipe version migration."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from autoskillit import __version__
from autoskillit.core import SkillResult, dump_yaml_str, get_logger
from autoskillit.migration.engine import (
    MIGRATE_RECIPES_MAX_RETRIES,
    HeadlessMigrationAdapter,
    MigrationFile,
    MigrationResult,
)
from autoskillit.migration.loader import applicable_migrations

logger = get_logger(__name__)


class RecipeMigrationAdapter(HeadlessMigrationAdapter):
    file_type = "recipe"

    def discover(self, project_dir: Path) -> list[MigrationFile]:
        from autoskillit.recipe import parse_recipe_metadata  # noqa: PLC0415

        recipes_dir = project_dir / ".autoskillit" / "recipes"
        if not recipes_dir.exists():
            return []
        files = []
        for p in sorted(recipes_dir.glob("*.yaml")):
            meta = parse_recipe_metadata(p)
            files.append(
                MigrationFile(
                    name=meta.name,
                    path=p,
                    file_type=self.file_type,
                    current_version=meta.version,
                )
            )
        return files

    def needs_migration(self, file: MigrationFile) -> bool:
        return bool(applicable_migrations(file.current_version, __version__))

    async def migrate(
        self,
        file: MigrationFile,
        *,
        run_headless: Callable[..., Awaitable[SkillResult]],
        temp_dir: Path,
    ) -> MigrationResult:
        migrations = applicable_migrations(file.current_version, __version__)
        if not migrations:
            return MigrationResult(success=True, name=file.name)

        notes_yaml = dump_yaml_str(
            [
                {
                    "from_version": m.from_version,
                    "to_version": m.to_version,
                    "description": m.description,
                    "changes": [
                        {
                            "id": c.id,
                            "description": c.description,
                            "instruction": c.instruction,
                            "detect": c.detect,
                            "example_before": c.example_before,
                            "example_after": c.example_after,
                        }
                        for c in m.changes
                    ],
                }
                for m in migrations
            ]
        )
        target_version = migrations[-1].to_version
        content = file.path.read_text()
        skill_command = (
            f"/autoskillit:migrate-recipes"
            f" script_path={file.path}"
            f" script_content={json.dumps(content)}"
            f" migration_notes={json.dumps(notes_yaml)}"
            f" target_version={target_version}"
        )

        raw = await run_headless(
            skill_command=skill_command,
            cwd=str(file.path.parent.parent.parent),
        )
        if not raw.success:
            return MigrationResult(
                success=False,
                name=file.name,
                error=raw.result or "headless session failed",
                retries_attempted=MIGRATE_RECIPES_MAX_RETRIES,
            )

        temp_out = self.get_temp_output_path(file, temp_dir)
        if not temp_out.exists():
            return MigrationResult(
                success=False,
                name=file.name,
                error="migrate-recipes did not produce output",
            )

        return MigrationResult(
            success=True,
            name=file.name,
            migrated_content=temp_out.read_text(),
        )

    def get_temp_output_path(self, file: MigrationFile, temp_dir: Path) -> Path:
        return temp_dir / "migrations" / f"{file.path.stem}.yaml"

    def validate(self, path: Path) -> tuple[bool, str]:
        from autoskillit.recipe import load_recipe as _parse_recipe  # noqa: PLC0415
        from autoskillit.recipe import validate_recipe_structure  # noqa: PLC0415

        try:
            recipe = _parse_recipe(path)
            errors = validate_recipe_structure(recipe)
            if errors:
                return False, "; ".join(str(e) for e in errors)
            return True, ""
        except Exception as exc:
            logger.warning("Recipe file validation failed", path=str(path), error=str(exc))
            return False, str(exc)
