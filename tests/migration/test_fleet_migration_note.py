from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.layer("migration"), pytest.mark.small]

MIGRATIONS_DIR = Path(__file__).parents[2] / "src" / "autoskillit" / "migrations"


def test_fleet_rename_migration_note_exists():
    """A migration note must cover the features.franchise → features.fleet rename."""
    notes = list(MIGRATIONS_DIR.glob("*.yaml"))
    detect_keys = []
    for note_path in notes:
        try:
            data = yaml.safe_load(note_path.read_text())
        except yaml.YAMLError as exc:
            pytest.fail(f"Malformed YAML in {note_path.name}: {exc}")
        for change in data.get("changes", []):
            detect = change.get("detect", {})
            if detect.get("key") == "features.franchise":
                detect_keys.append(note_path.name)
    assert detect_keys, (
        "No migration note found with detect.key == 'features.franchise'. "
        "Create src/autoskillit/migrations/0.9.134-to-0.9.135.yaml."
    )


def test_fleet_rename_migration_note_is_valid():
    """Migration note for fleet rename must have correct required fields."""
    notes = list(MIGRATIONS_DIR.glob("*.yaml"))
    for note_path in notes:
        try:
            data = yaml.safe_load(note_path.read_text())
        except yaml.YAMLError as exc:
            pytest.fail(f"Malformed YAML in {note_path.name}: {exc}")
        for change in data.get("changes", []):
            if change.get("detect", {}).get("key") == "features.franchise":
                assert "instruction" in change, "Migration note missing 'instruction'"
                assert "example_before" in change, "Migration note missing 'example_before'"
                assert "example_after" in change, "Migration note missing 'example_after'"
                return
    pytest.fail("No matching migration change found")
