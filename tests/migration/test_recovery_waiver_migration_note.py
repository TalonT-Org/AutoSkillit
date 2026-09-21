"""The recovery waiver migration note describes the new merge routing contract."""

import pytest

from autoskillit.migration.loader import list_migrations

pytestmark = [pytest.mark.layer("migration"), pytest.mark.small]


def test_recovery_waiver_migration_note_is_complete() -> None:
    changes = [
        change
        for note in list_migrations()
        for change in note.changes
        if change.id == "recovery-waiver-required"
    ]
    assert len(changes) == 1
    change = changes[0]
    assert change.detect["tool"] == "merge_worktree"
    assert change.instruction.strip()
    assert change.example_before.strip()
    assert change.example_after.strip()
