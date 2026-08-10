import pytest

from autoskillit.core import load_yaml, pkg_root
from autoskillit.migration.loader import applicable_migrations

pytestmark = [pytest.mark.layer("migration"), pytest.mark.small]


def test_on_skip_migration_is_broad_and_requires_author_intent() -> None:
    note_path = pkg_root() / "migrations" / "0.0.0-to-0.10.952.yaml"
    note = load_yaml(note_path)
    change = note["changes"][0]

    assert note["from_version"] == "0.0.0"
    assert note["to_version"] == "0.10.952"
    assert change["detect"] == {"missing_field": "on_skip"}
    instruction = change["instruction"]
    assert "skip_when_false" in instruction
    assert "author-selected" in instruction
    assert "Do not copy" in instruction
    assert "manual author action" in instruction


@pytest.mark.parametrize("version", ["0.10.951", "0.7.0"])
def test_on_skip_migration_is_discoverable_from_supported_versions(version: str) -> None:
    notes = applicable_migrations(version, "0.10.952")
    assert any(note.to_version == "0.10.952" for note in notes)
