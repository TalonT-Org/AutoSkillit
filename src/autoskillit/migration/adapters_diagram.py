"""Diagram migration adapter — advisory staleness detection for skill-crafted diagrams.

The diagram adapter never overwrites files; it surfaces a suggestion to run
``/render-recipe`` instead. ``AdvisoryResult`` is defined locally here because
the diagram adapter is the sole producer of this shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import regex as re

from autoskillit.migration.engine import AdvisoryMigrationAdapter, MigrationFile


@dataclass
class AdvisoryResult:
    name: str
    suggestion: str


class DiagramMigrationAdapter(AdvisoryMigrationAdapter):
    """Advisory adapter for skill-crafted recipe flow diagrams.

    Detects stale diagrams but never overwrites them — returns a suggestion
    to run ``/render-recipe`` instead.
    """

    file_type = "diagram"

    def discover(self, project_dir: Path) -> list[MigrationFile]:
        diagrams_dir = project_dir / ".autoskillit" / "recipes" / "diagrams"
        if not diagrams_dir.is_dir():
            return []
        return [
            MigrationFile(name=p.stem, path=p, file_type="diagram", current_version=None)
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
        return AdvisoryResult(
            name=file.name,
            suggestion=suggestions[0]["message"] if suggestions else "",
        )

    def validate(self, path: Path) -> tuple[bool, str]:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            return False, str(exc)
        if not re.search(r"<!-- autoskillit-recipe-hash: sha256:[0-9a-f]+ -->", content):
            return False, "missing autoskillit-recipe-hash comment"
        return True, ""
