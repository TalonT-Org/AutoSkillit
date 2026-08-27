"""Tests for the DiagramMigrationAdapter and diagram-specific advisory paths."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from autoskillit.migration.adapters_diagram import DiagramMigrationAdapter
from autoskillit.migration.engine import (
    AdvisoryResult,
    DeterministicMigrationAdapter,
    MigrationEngine,
    MigrationFile,
)
from autoskillit.migration.service import default_migration_engine

pytestmark = [pytest.mark.layer("migration"), pytest.mark.small]

_SAMPLE_RECIPE_YAML_FOR_DIAG: str = """\
name: my-recipe
description: A test recipe
summary: step1 -> done
ingredients:
  task:
    description: What to do
    required: true
steps:
  step1:
    tool: run_skill
    with:
      skill_command: "/autoskillit:investigate ${{ inputs.task }}"
      cwd: "."
    on_success: done
    on_failure: escalate
  done:
    action: stop
    message: "Done."
  escalate:
    action: stop
    message: "Failed."
kitchen_rules:
  - "Use AutoSkillit tools only"
"""


@pytest.fixture
def sample_recipe_yaml_for_diagram(tmp_path: Path) -> Path:
    """Write _SAMPLE_RECIPE_YAML_FOR_DIAG to a yaml file in tmp_path."""
    target = tmp_path / "diagram_recipe.yaml"
    target.write_text(_SAMPLE_RECIPE_YAML_FOR_DIAG, encoding="utf-8")
    return target


class TestDiagramMigrationAdapter:
    # DG-16
    def test_diagram_adapter_discover_finds_md_files(self, tmp_path: Path) -> None:
        """DG-16: DiagramMigrationAdapter.discover() finds .md files in diagrams/."""
        diag_dir = tmp_path / ".autoskillit" / "recipes" / "diagrams"
        diag_dir.mkdir(parents=True)
        (diag_dir / "my-recipe.md").write_text("<!-- autoskillit-recipe-hash: sha256:abc -->")
        adapter = DiagramMigrationAdapter()
        files = adapter.discover(tmp_path)
        assert len(files) == 1
        assert files[0].name == "my-recipe"
        assert files[0].file_type == "diagram"

    # DG-17
    def test_diagram_adapter_discover_returns_empty_when_dir_missing(self, tmp_path: Path) -> None:
        """DG-17: DiagramMigrationAdapter.discover() returns [] when dir missing."""
        adapter = DiagramMigrationAdapter()
        assert adapter.discover(tmp_path) == []

    # DG-18
    def test_diagram_adapter_needs_migration_stale(
        self, tmp_path: Path, sample_recipe_yaml_for_diagram: Path
    ) -> None:
        """DG-18: DiagramMigrationAdapter.needs_migration() True when diagram stale."""
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        diagrams_dir = recipes_dir / "diagrams"
        diagrams_dir.mkdir(parents=True)
        import shutil

        shutil.copy2(sample_recipe_yaml_for_diagram, recipes_dir / "my-recipe.yaml")
        # Write diagram with a wrong hash (stale)
        (diagrams_dir / "my-recipe.md").write_text(
            "<!-- autoskillit-recipe-hash: sha256:wronghashvalue -->\n## my-recipe\n"
        )
        file = MigrationFile(
            name="my-recipe",
            path=diagrams_dir / "my-recipe.md",
            file_type="diagram",
            current_version=None,
        )
        assert DiagramMigrationAdapter().needs_migration(file) is True

    # DG-19 (replaced: advisory path instead of destructive migrate)
    def test_diagram_adapter_check_staleness_returns_advisory(self) -> None:
        """DG-19: DiagramMigrationAdapter.check_staleness() returns AdvisoryResult."""
        file = MigrationFile(
            name="my-recipe",
            path=Path("/fake/diagrams/my-recipe.md"),
            file_type="diagram",
            current_version=None,
        )
        result = DiagramMigrationAdapter().check_staleness(file)
        assert isinstance(result, AdvisoryResult)
        assert "/render-recipe" in result.suggestion
        assert "my-recipe" in result.suggestion

    def test_diagram_adapter_validate_passes_when_hash_present(self, tmp_path: Path) -> None:
        """DG-20: DiagramMigrationAdapter.validate() passes when hash comment present."""
        md = tmp_path / "test.md"
        md.write_text("<!-- autoskillit-recipe-hash: sha256:abc123def456 -->\n## My Recipe\n")
        adapter = DiagramMigrationAdapter()
        valid, msg = adapter.validate(md)
        assert valid is True
        assert msg == ""

    def test_diagram_adapter_validate_fails_when_hash_absent(self, tmp_path: Path) -> None:
        """validate() fails when hash comment missing."""
        md = tmp_path / "test.md"
        md.write_text("## My Recipe\nNo hash here.\n")
        adapter = DiagramMigrationAdapter()
        valid, msg = adapter.validate(md)
        assert valid is False
        assert "missing" in msg

    def test_diagram_adapter_validate_fails_on_undecodable_bytes(self, tmp_path: Path) -> None:
        """validate() reports a failure instead of raising on non-UTF-8 content.

        ``UnicodeDecodeError`` is a ``ValueError``, not an ``OSError``, so a
        narrow ``except OSError`` would let it escape and break the
        ``tuple[bool, str]`` contract every caller relies on.
        """
        md = tmp_path / "test.md"
        md.write_bytes(b"\xff\xfe not valid utf-8")
        adapter = DiagramMigrationAdapter()
        valid, msg = adapter.validate(md)
        assert valid is False
        assert msg != ""

    def test_default_engine_includes_diagram_adapter(self) -> None:
        """default_migration_engine() registers the DiagramMigrationAdapter."""
        engine = default_migration_engine()
        assert isinstance(engine.get_adapter("diagram"), DiagramMigrationAdapter)


def test_diagram_adapter_type_is_not_deterministic() -> None:
    """T-ADAPTER-TYPE: DiagramMigrationAdapter must NOT be a DeterministicMigrationAdapter."""
    assert not isinstance(DiagramMigrationAdapter(), DeterministicMigrationAdapter)


@pytest.mark.anyio
async def test_advisory_dispatch_does_not_write_file(tmp_path: Path) -> None:
    """T-ADVISORY-DISPATCH: MigrationEngine returns advisory for stale diagrams without writing."""
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    diagrams_dir = recipes_dir / "diagrams"
    diagrams_dir.mkdir(parents=True)
    recipe_yaml = recipes_dir / "my-recipe.yaml"
    recipe_yaml.write_text(_SAMPLE_RECIPE_YAML_FOR_DIAG)

    original_content = (
        "<!-- autoskillit-recipe-hash: sha256:wronghash -->\n## my-recipe\nASCII art here\n"
    )
    diagram_md = diagrams_dir / "my-recipe.md"
    diagram_md.write_text(original_content)

    file = MigrationFile(
        name="my-recipe",
        path=diagram_md,
        file_type="diagram",
        current_version=None,
    )
    engine = MigrationEngine([DiagramMigrationAdapter()])
    result = await engine.migrate_file(
        file,
        run_headless=AsyncMock(),
        temp_dir=tmp_path / "temp",
    )
    assert result.success is True
    assert result.advisory is not None
    assert "/render-recipe" in result.advisory
    assert diagram_md.read_text() == original_content
