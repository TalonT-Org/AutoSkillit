"""Diagram migration adapter — advisory staleness detection for skill-crafted diagrams."""

from __future__ import annotations

from pathlib import Path

import regex as re

from autoskillit.core import get_logger
from autoskillit.migration.engine import (
    AdvisoryMigrationAdapter,
    AdvisoryResult,
    MigrationFile,
)

logger = get_logger(__name__)


class DiagramMigrationAdapter(AdvisoryMigrationAdapter):
    """Advisory adapter that flags stale recipe flow diagrams without overwriting them."""

    file_type = "diagram"

    def discover(self, project_dir: Path) -> list[MigrationFile]:
        diagrams_dir = project_dir / ".autoskillit" / "recipes" / "diagrams"
        if not diagrams_dir.is_dir():
            return []
        return [
            MigrationFile(name=p.stem, path=p, file_type=self.file_type, current_version=None)
            for p in sorted(diagrams_dir.glob("*.md"))
        ]

    def needs_migration(self, file: MigrationFile) -> bool:
        from autoskillit.recipe import check_diagram_staleness  # noqa: PLC0415

        if not file.path.exists():
            return False
        recipes_dir = file.path.parent.parent
        recipe_path = recipes_dir / f"{file.name}.yaml"
        if not recipe_path.exists():
            return False
        return check_diagram_staleness(file.name, recipes_dir, recipe_path)

    def check_staleness(self, file: MigrationFile) -> AdvisoryResult:
        from autoskillit.recipe import diagram_stale_to_suggestions  # noqa: PLC0415

        suggestions = diagram_stale_to_suggestions(file.name)
        if not suggestions:
            logger.warning(
                "diagram.stale_detector_returned_no_suggestions",
                name=file.name,
            )
        return AdvisoryResult(
            name=file.name,
            suggestion=suggestions[0]["message"] if suggestions else "",
        )

    def validate(self, path: Path) -> tuple[bool, str]:
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning(
                "Diagram file validation failed", path=str(path), error=str(exc), exc_info=True
            )
            return False, str(exc)
        if not re.search(r"<!-- autoskillit-recipe-hash: sha256:[0-9a-f]+ -->", content):
            return False, "missing autoskillit-recipe-hash comment"
        return True, ""
