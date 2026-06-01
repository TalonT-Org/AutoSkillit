from pathlib import Path

import pytest
import yaml

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("migration"), pytest.mark.small]

MIGRATIONS_DIR = Path(__file__).parents[2] / "src" / "autoskillit" / "migrations"


def test_vis_lens_rename_migration_note_exists():
    """Migration note covers vis-lens-domain-norms → vis-lens-methodology-norms rename."""
    notes = list(MIGRATIONS_DIR.glob("*.yaml"))
    matches = []
    for note_path in notes:
        try:
            data = load_yaml(note_path)
        except yaml.YAMLError as exc:
            pytest.fail(f"Malformed YAML in {note_path.name}: {exc}")
        for change in data.get("changes", []):
            detect = change.get("detect", {})
            if detect.get("skill_pattern") == "vis-lens-domain-norms":
                matches.append(note_path.name)
    assert matches, (
        "No migration note found with detect.skill_pattern == 'vis-lens-domain-norms'. "
        "Create src/autoskillit/migrations/<from>-to-<to>.yaml with a "
        "vis-lens-methodology-norms-rename change entry."
    )


def test_vis_lens_rename_migration_note_is_valid():
    """Migration note for vis-lens rename must have instruction and examples."""
    notes = list(MIGRATIONS_DIR.glob("*.yaml"))
    for note_path in notes:
        try:
            data = load_yaml(note_path)
        except yaml.YAMLError as exc:
            pytest.fail(f"Malformed YAML in {note_path.name}: {exc}")
        for change in data.get("changes", []):
            if change.get("detect", {}).get("skill_pattern") == "vis-lens-domain-norms":
                assert "instruction" in change, "Migration note missing 'instruction'"
                assert change["instruction"].strip(), "Migration note 'instruction' is empty"
                assert "example_before" in change, "Migration note missing 'example_before'"
                assert change["example_before"].strip(), "Migration note 'example_before' is empty"
                assert "example_after" in change, "Migration note missing 'example_after'"
                assert change["example_after"].strip(), "Migration note 'example_after' is empty"
                return
    pytest.fail(
        "No matching migration change found with detect.skill_pattern == 'vis-lens-domain-norms'"
    )
