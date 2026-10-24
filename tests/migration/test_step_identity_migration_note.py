"""The step identity migration reaches recipes on the preceding release."""

import pytest

from autoskillit.migration.loader import applicable_migrations

pytestmark = [pytest.mark.layer("migration"), pytest.mark.small]


@pytest.mark.parametrize("recipe_version", ["0.10.1288", "0.10.1289", "0.10.1290"])
def test_step_identity_migration_applies_on_upgrade(recipe_version: str) -> None:
    notes = applicable_migrations(recipe_version, "0.10.1291")
    matching = [
        note
        for note in notes
        if any(change.id == "step-name-equals-step-key" for change in note.changes)
    ]

    assert len(matching) == 1
    assert matching[0].to_version == "0.10.1291"
