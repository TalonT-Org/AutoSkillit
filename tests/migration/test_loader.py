"""Tests for migration note discovery and version chaining."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autoskillit.migration.loader import (
    _parse_migration,
    applicable_migrations,
    list_migrations,
)

pytestmark = [pytest.mark.layer("migration"), pytest.mark.small]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_CHANGE = {
    "id": "some-change",
    "description": "What changed",
    "instruction": "How to fix",
    "detect": {"tool": "run_skill", "skill_pattern": "something"},
    "example_before": "old yaml",
    "example_after": "new yaml",
}

VALID_MIGRATION = {
    "from_version": "0.0.0",
    "to_version": "0.1.0",
    "description": "Initial version stamp",
    "changes": [VALID_CHANGE],
}


def _write_migration(path: Path, data: dict) -> Path:
    path.write_text(yaml.dump(data, default_flow_style=False))
    return path


def _make_migrations_dir(tmp_path: Path) -> Path:
    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir()
    return mig_dir


# ---------------------------------------------------------------------------
# ML1-ML3: MigrationNote and MigrationChange dataclass construction
# ---------------------------------------------------------------------------


class TestMigrationNote:
    """MigrationNote and MigrationChange dataclass construction."""

    # ML1
    def test_parse_migration_parses_all_fields(self, tmp_path: Path) -> None:
        """MigrationNote from valid YAML parses all fields."""
        path = _write_migration(tmp_path / "0.0.0-to-0.1.0.yaml", VALID_MIGRATION)
        note = _parse_migration(path)

        assert note.from_version == "0.0.0"
        assert note.to_version == "0.1.0"
        assert note.description == "Initial version stamp"
        assert len(note.changes) == 1
        assert note.path == path

    # ML2
    def test_migration_change_captures_all_fields(self, tmp_path: Path) -> None:
        """MigrationChange captures id, description, instruction, detect, examples."""
        path = _write_migration(tmp_path / "mig.yaml", VALID_MIGRATION)
        note = _parse_migration(path)
        change = note.changes[0]

        assert change.id == "some-change"
        assert change.description == "What changed"
        assert change.instruction == "How to fix"
        assert change.detect == {"tool": "run_skill", "skill_pattern": "something"}
        assert change.example_before == "old yaml"
        assert change.example_after == "new yaml"

    # ML3
    def test_missing_from_version_raises_value_error(self, tmp_path: Path) -> None:
        """Missing from_version raises ValueError."""
        data = {
            "to_version": "0.1.0",
            "description": "Missing from_version",
            "changes": [],
        }
        path = _write_migration(tmp_path / "bad.yaml", data)
        with pytest.raises(ValueError, match="from_version"):
            _parse_migration(path)

    def test_missing_to_version_raises_value_error(self, tmp_path: Path) -> None:
        """Missing to_version raises ValueError."""
        data = {
            "from_version": "0.0.0",
            "description": "Missing to_version",
            "changes": [],
        }
        path = _write_migration(tmp_path / "bad.yaml", data)
        with pytest.raises(ValueError, match="to_version"):
            _parse_migration(path)

    def test_non_mapping_yaml_raises_value_error(self, tmp_path: Path) -> None:
        """A YAML file with a list at top level raises ValueError."""
        path = tmp_path / "list.yaml"
        path.write_text("- item1\n- item2\n")
        with pytest.raises(ValueError, match="mapping"):
            _parse_migration(path)

    def test_change_missing_id_raises_value_error(self, tmp_path: Path) -> None:
        """A change entry without an id raises ValueError."""
        data = {
            "from_version": "0.0.0",
            "to_version": "0.1.0",
            "description": "Bad change",
            "changes": [{"description": "no id here", "instruction": "fix it"}],
        }
        path = _write_migration(tmp_path / "bad.yaml", data)
        with pytest.raises(ValueError, match="id"):
            _parse_migration(path)

    def test_optional_change_fields_default_to_empty(self, tmp_path: Path) -> None:
        """detect, example_before, example_after default to empty when absent."""
        data = {
            "from_version": "0.0.0",
            "to_version": "0.1.0",
            "description": "Minimal migration",
            "changes": [{"id": "minimal"}],
        }
        path = _write_migration(tmp_path / "minimal.yaml", data)
        note = _parse_migration(path)
        change = note.changes[0]

        assert change.detect == {}
        assert change.example_before == ""
        assert change.example_after == ""

    def test_changes_list_defaults_to_empty(self, tmp_path: Path) -> None:
        """Migration with no changes key produces an empty changes list."""
        data = {
            "from_version": "0.0.0",
            "to_version": "0.1.0",
            "description": "No changes",
        }
        path = _write_migration(tmp_path / "nochanges.yaml", data)
        note = _parse_migration(path)
        assert note.changes == []


# ---------------------------------------------------------------------------
# ML4-ML6: Discovery of migration YAML files from package directory
# ---------------------------------------------------------------------------


class TestListMigrations:
    """Discovery of migration YAML files from package directory."""

    # ML4
    def test_empty_list_when_no_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns empty list when migrations/ dir has no files."""
        mig_dir = _make_migrations_dir(tmp_path)
        monkeypatch.setattr("autoskillit.migration.loader._migrations_dir", lambda: mig_dir)
        assert list_migrations() == []

    # ML5
    def test_discovers_yaml_files_sorted_by_filename(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Discovers .yaml files sorted by filename."""
        mig_dir = _make_migrations_dir(tmp_path)
        _write_migration(
            mig_dir / "0.2.0.yaml",
            {"from_version": "0.1.0", "to_version": "0.2.0", "description": "Second"},
        )
        _write_migration(
            mig_dir / "0.1.0.yaml",
            {"from_version": "0.0.0", "to_version": "0.1.0", "description": "First"},
        )
        monkeypatch.setattr("autoskillit.migration.loader._migrations_dir", lambda: mig_dir)

        notes = list_migrations()
        assert len(notes) == 2
        assert notes[0].to_version == "0.1.0"
        assert notes[1].to_version == "0.2.0"

    # ML6
    def test_skips_non_yaml_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Skips non-YAML files in the migrations directory."""
        mig_dir = _make_migrations_dir(tmp_path)
        _write_migration(
            mig_dir / "0.1.0.yaml",
            {"from_version": "0.0.0", "to_version": "0.1.0", "description": "Valid"},
        )
        (mig_dir / "README.md").write_text("# Migrations")
        (mig_dir / "notes.txt").write_text("some notes")
        monkeypatch.setattr("autoskillit.migration.loader._migrations_dir", lambda: mig_dir)

        notes = list_migrations()
        assert len(notes) == 1
        assert notes[0].to_version == "0.1.0"

    def test_empty_when_migrations_dir_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns empty list when the migrations/ directory does not exist."""
        missing_dir = tmp_path / "nonexistent"
        monkeypatch.setattr("autoskillit.migration.loader._migrations_dir", lambda: missing_dir)
        assert list_migrations() == []


# ---------------------------------------------------------------------------
# ML7-ML12: Version chaining for applicable_migrations
# ---------------------------------------------------------------------------


class TestApplicableMigrations:
    """Version chaining for migration notes."""

    def _setup_migrations(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, migrations: list[dict]
    ) -> None:
        """Write migration files to a temp directory and monkeypatch _migrations_dir."""
        mig_dir = _make_migrations_dir(tmp_path)
        for mig in migrations:
            filename = f"{mig['from_version']}-to-{mig['to_version']}.yaml"
            _write_migration(mig_dir / filename, mig)
        monkeypatch.setattr("autoskillit.migration.loader._migrations_dir", lambda: mig_dir)

    # ML7
    def test_script_at_from_version_matches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """from_version="0.1.0", to_version="0.2.0" matches script at "0.1.0"."""
        self._setup_migrations(
            tmp_path,
            monkeypatch,
            [{"from_version": "0.1.0", "to_version": "0.2.0", "description": "Step"}],
        )
        result = applicable_migrations("0.1.0", "0.2.0")
        assert len(result) == 1
        assert result[0].to_version == "0.2.0"

    # ML8
    def test_script_at_to_version_does_not_match(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """from_version="0.1.0", to_version="0.2.0" does NOT match script at "0.2.0"."""
        self._setup_migrations(
            tmp_path,
            monkeypatch,
            [{"from_version": "0.1.0", "to_version": "0.2.0", "description": "Step"}],
        )
        result = applicable_migrations("0.2.0", "0.2.0")
        assert result == []

    # ML9
    def test_none_version_not_migratable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Recipes with no version (None) are not subject to migration."""
        self._setup_migrations(
            tmp_path,
            monkeypatch,
            [
                {
                    "from_version": "0.0.0",
                    "to_version": "0.1.0",
                    "description": "Initial",
                }
            ],
        )
        result = applicable_migrations(None, "0.1.0")
        assert result == []

    # ML10
    def test_script_mid_range_matches_enclosing_migration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Script at "0.1.5" matches migration from "0.1.0" to "0.2.0" (mid-range)."""
        self._setup_migrations(
            tmp_path,
            monkeypatch,
            [
                {
                    "from_version": "0.1.0",
                    "to_version": "0.2.0",
                    "description": "Mid-range",
                }
            ],
        )
        result = applicable_migrations("0.1.5", "0.2.0")
        assert len(result) == 1
        assert result[0].to_version == "0.2.0"

    # ML11
    def test_chained_migrations_both_apply(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Chained migrations: 0.1.0->0.2.0 then 0.2.0->0.3.0 both apply to script at "0.1.0"."""
        self._setup_migrations(
            tmp_path,
            monkeypatch,
            [
                {
                    "from_version": "0.1.0",
                    "to_version": "0.2.0",
                    "description": "First hop",
                },
                {
                    "from_version": "0.2.0",
                    "to_version": "0.3.0",
                    "description": "Second hop",
                },
            ],
        )
        result = applicable_migrations("0.1.0", "0.3.0")
        assert len(result) == 2
        assert result[0].to_version == "0.2.0"
        assert result[1].to_version == "0.3.0"

    # ML12
    def test_gap_in_chain_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Gap in chain: script at "0.1.0", only migration 0.2.0->0.3.0 — returns empty."""
        self._setup_migrations(
            tmp_path,
            monkeypatch,
            [
                {
                    "from_version": "0.2.0",
                    "to_version": "0.3.0",
                    "description": "Gap migration",
                }
            ],
        )
        result = applicable_migrations("0.1.0", "0.3.0")
        assert result == []

    def test_no_migrations_available_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns empty list when no migration files exist."""
        mig_dir = _make_migrations_dir(tmp_path)
        monkeypatch.setattr("autoskillit.migration.loader._migrations_dir", lambda: mig_dir)
        result = applicable_migrations("0.1.0", "0.2.0")
        assert result == []

    def test_none_version_with_no_matching_migration_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """None script version with no 0.0.0 migration returns empty."""
        self._setup_migrations(
            tmp_path,
            monkeypatch,
            [
                {
                    "from_version": "0.1.0",
                    "to_version": "0.2.0",
                    "description": "Skipped",
                }
            ],
        )
        result = applicable_migrations(None, "0.2.0")
        assert result == []
