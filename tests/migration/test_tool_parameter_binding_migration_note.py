"""The tool-parameter migration note reaches recipes from supported releases."""

import pytest

from autoskillit.migration.loader import applicable_migrations

pytestmark = [pytest.mark.layer("migration"), pytest.mark.small]


@pytest.mark.parametrize("recipe_version", ["0.10.1200", "0.10.1288", "0.10.1291", "0.10.1292"])
def test_tool_parameter_binding_migration_applies_on_upgrade(recipe_version: str) -> None:
    to_version = "0.10.1293"
    notes = applicable_migrations(recipe_version, to_version)
    matching = [
        note
        for note in notes
        if any(change.id == "required-tool-parameters-declared" for change in note.changes)
    ]

    assert len(matching) == 1
    assert matching[0].to_version == to_version
