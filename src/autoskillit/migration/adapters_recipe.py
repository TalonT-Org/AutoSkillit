"""Recipe migration adapter — headless LLM-driven recipe version migration."""

from __future__ import annotations

import json
from pathlib import Path

from autoskillit import __version__
from autoskillit.core import (
    RecipePathValidationReport,
    dump_yaml_str,
    get_logger,
    is_recipe_path_validation_report,
    temp_dir_display_str,
)
from autoskillit.migration.engine import (
    HeadlessMigrationAdapter,
    HeadlessRunner,
    MigrationFile,
    MigrationResult,
)
from autoskillit.migration.loader import applicable_migrations

logger = get_logger(__name__)


def _completed_report_errors(result: RecipePathValidationReport) -> dict[str, str]:
    errors = {f"structural:{message}": message for message in result["errors"]}
    for finding in result["findings"]:
        if finding.get("severity") == "error":
            message = finding.get("message", "")
            key = f"semantic:{finding.get('rule', '')}@{finding.get('step', '')}:{message}"
            errors[key] = message
    for finding in result["contracts"]:
        if finding.get("severity") == "error":
            message = str(finding.get("message", ""))
            key = f"contract:{finding.get('rule', '')}@{finding.get('step', '')}:{message}"
            errors[key] = message
    for failure in result["binding_projection_failures"]:
        message = failure.get("message", "")
        key = (
            f"binding:{failure.get('code', '')}@{failure.get('step_name', '')}"
            f"@{failure.get('name', '')}:{message}"
        )
        errors[key] = message
    return errors


class RecipeMigrationAdapter(HeadlessMigrationAdapter):
    """Headless adapter for LLM-driven recipe version migration."""

    file_type = "recipe"

    def __init__(self, *, temp_dir_override: str | None = None) -> None:
        self._temp_dir_display = temp_dir_display_str(temp_dir_override)

    def discover(self, project_dir: Path) -> list[MigrationFile]:
        from autoskillit.recipe import parse_recipe_metadata

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
        run_headless: HeadlessRunner,
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
        baseline = self._validation_errors(file.path)
        content = file.path.read_text()
        skill_command = (
            f"/autoskillit:migrate-recipes"
            f" script_path={file.path}"
            f" script_content={json.dumps(content)}"
            f" migration_notes={json.dumps(notes_yaml)}"
            f" target_version={target_version}"
        )

        temp_out = self.get_temp_output_path(file, temp_dir)
        temp_out.unlink(missing_ok=True)
        raw = await run_headless(
            skill_command=skill_command,
            cwd=str(file.path.parent.parent.parent),
        )
        if not raw.success:
            return MigrationResult(
                success=False,
                name=file.name,
                error=raw.result or "headless session failed",
                retries_attempted=0,
            )

        if not temp_out.exists():
            return MigrationResult(
                success=False,
                name=file.name,
                error="migrate-recipes did not produce output",
            )

        try:
            from autoskillit.recipe import parse_recipe_metadata

            if parse_recipe_metadata(temp_out).version != target_version:
                return MigrationResult(
                    success=False,
                    name=file.name,
                    error=(
                        f"migrated recipe is not stamped with autoskillit_version {target_version}"
                    ),
                )

            output_errors = self._validation_errors(
                temp_out,
                contract_source_path=file.path,
            )
            usable_baseline = {} if "exception" in baseline else baseline
            new_errors = {
                key: message
                for key, message in output_errors.items()
                if key not in usable_baseline
            }
            if new_errors:
                summary = "; ".join(message[:500] for message in list(new_errors.values())[:5])
                return MigrationResult(
                    success=False,
                    name=file.name,
                    error=f"migrated recipe introduced validation errors: {summary}",
                )
            return MigrationResult(
                success=True,
                name=file.name,
                migrated_content=temp_out.read_text(),
            )
        except Exception as exc:
            logger.warning(
                "Migrated recipe validation failed",
                path=str(temp_out),
                error=str(exc),
                exc_info=True,
            )
            return MigrationResult(
                success=False,
                name=file.name,
                error=f"{type(exc).__name__}: {exc}",
            )

    def get_temp_output_path(self, file: MigrationFile, temp_dir: Path) -> Path:
        return temp_dir / "migrations" / f"{file.path.stem}.yaml"

    def validate(self, path: Path) -> tuple[bool, str]:
        errors = self._validation_errors(path)
        return not errors, "; ".join(errors.values())

    def _validation_errors(
        self,
        path: Path,
        *,
        contract_source_path: Path | None = None,
    ) -> dict[str, str]:
        from autoskillit.recipe import load_recipe, validate_from_path, validate_recipe_structure

        errors: dict[str, str] = {}
        try:
            recipe = load_recipe(path)
            errors.update(
                (f"structural:{message}", message) for message in validate_recipe_structure(recipe)
            )
        except Exception as exc:
            logger.warning(
                "Recipe file validation failed", path=str(path), error=str(exc), exc_info=True
            )
            errors["exception"] = f"{type(exc).__name__}: {exc}"

        try:
            result = validate_from_path(
                path,
                temp_dir_relpath=self._temp_dir_display,
                contract_source_path=contract_source_path,
            )
            if is_recipe_path_validation_report(result):
                errors.update(_completed_report_errors(result))
            else:
                errors.update(
                    (f"input:{finding['error']}", finding["error"])
                    for finding in result["findings"]
                )
        except Exception as exc:
            logger.warning(
                "Recipe file validation failed", path=str(path), error=str(exc), exc_info=True
            )
            errors["exception"] = f"{type(exc).__name__}: {exc}"
        return errors
