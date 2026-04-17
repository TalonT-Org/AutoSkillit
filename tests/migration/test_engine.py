"""Tests for migration_engine.py — ME1 through ME21."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.core.types import RetryReason
from autoskillit.execution.session import SkillResult
from autoskillit.migration.engine import (
    MIGRATE_RECIPES_MAX_RETRIES,
    ContractMigrationAdapter,
    DeterministicMigrationAdapter,
    DiagramMigrationAdapter,
    HeadlessMigrationAdapter,
    MigrationAdapter,
    MigrationFile,
    RecipeMigrationAdapter,
    default_migration_engine,
)
from autoskillit.migration.loader import MigrationChange, MigrationNote

pytestmark = [pytest.mark.layer("migration"), pytest.mark.small]

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill_result(success: bool, result: str = "") -> SkillResult:
    """Create a minimal SkillResult for testing headless return values."""
    return SkillResult(
        success=success,
        result=result,
        session_id="",
        subtype="success" if success else "error",
        is_error=not success,
        exit_code=0 if success else 1,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
    )


def _make_migration_note(
    from_version: str = "0.0.0",
    to_version: str = "1.0.0",
    tmp_path: Path | None = None,
) -> MigrationNote:
    return MigrationNote(
        from_version=from_version,
        to_version=to_version,
        description="test migration",
        changes=[
            MigrationChange(
                id="CH1",
                description="test change",
                instruction="do something",
            )
        ],
        path=Path("/fake/migration.yaml"),
    )


# ---------------------------------------------------------------------------
# RecipeMigrationAdapter tests (ME1–ME9)
# ---------------------------------------------------------------------------


class TestRecipeMigrationAdapter:
    # ME1
    def test_recipe_adapter_discover_finds_recipes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "alpha.yaml").write_text("name: alpha\n")
        (recipes_dir / "beta.yaml").write_text("name: beta\n")
        # contracts subdir — should NOT be picked up
        contracts_dir = recipes_dir / "contracts"
        contracts_dir.mkdir()
        (contracts_dir / "contract.yaml").write_text("skill_hashes: {}")

        adapter = RecipeMigrationAdapter()
        files = adapter.discover(tmp_path)

        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"alpha", "beta"}
        assert all(f.file_type == "recipe" for f in files)

    # ME2
    def test_recipe_adapter_discover_empty_dir(self, tmp_path: Path) -> None:
        adapter = RecipeMigrationAdapter()
        files = adapter.discover(tmp_path)
        assert files == []

    # ME3
    def test_recipe_adapter_needs_migration_when_outdated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "autoskillit.migration.engine.applicable_migrations",
            lambda *a, **kw: [_make_migration_note()],
        )
        file = MigrationFile(
            name="test", path=tmp_path / "test.yaml", file_type="recipe", current_version="0.0.1"
        )
        adapter = RecipeMigrationAdapter()
        assert adapter.needs_migration(file) is True

    # ME4
    def test_recipe_adapter_no_migration_when_current(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "autoskillit.migration.engine.applicable_migrations",
            lambda *a, **kw: [],
        )
        file = MigrationFile(
            name="test", path=tmp_path / "test.yaml", file_type="recipe", current_version="99.0.0"
        )
        adapter = RecipeMigrationAdapter()
        assert adapter.needs_migration(file) is False

    # ME5
    def test_recipe_adapter_needs_migration_when_no_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # current_version=None is treated as 0.0.0 by applicable_migrations;
        # we return a non-empty list to verify that None still causes needs_migration=True
        monkeypatch.setattr(
            "autoskillit.migration.engine.applicable_migrations",
            lambda *a, **kw: [_make_migration_note()],
        )
        file = MigrationFile(
            name="test", path=tmp_path / "test.yaml", file_type="recipe", current_version=None
        )
        adapter = RecipeMigrationAdapter()
        assert adapter.needs_migration(file) is True

    # ME6
    @pytest.mark.anyio
    async def test_recipe_adapter_build_skill_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recipe_path = tmp_path / ".autoskillit" / "recipes" / "myrecipe.yaml"
        recipe_path.parent.mkdir(parents=True)
        recipe_path.write_text("name: myrecipe\n")

        # Pre-create temp output so migrate() doesn't return "no output" failure
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_out = temp_dir / "migrations" / "myrecipe.yaml"
        temp_out.parent.mkdir(parents=True)
        temp_out.write_text("name: myrecipe\n# migrated\n")

        monkeypatch.setattr(
            "autoskillit.migration.engine.applicable_migrations",
            lambda *a, **kw: [_make_migration_note()],
        )
        mock_headless = AsyncMock(return_value=_make_skill_result(True))

        adapter = RecipeMigrationAdapter()
        file = MigrationFile(
            name="myrecipe", path=recipe_path, file_type="recipe", current_version="0.0.1"
        )
        await adapter.migrate(file, run_headless=mock_headless, temp_dir=temp_dir)

        assert mock_headless.await_count == 1
        call_kwargs = mock_headless.call_args.kwargs
        assert "skill_command" in call_kwargs
        skill_cmd: str = call_kwargs["skill_command"]
        assert "script_path=" in skill_cmd
        assert "script_content=" in skill_cmd
        assert "migration_notes=" in skill_cmd
        assert "target_version=" in skill_cmd

    # ME7
    def test_recipe_adapter_temp_output_path(self, tmp_path: Path) -> None:
        adapter = RecipeMigrationAdapter()
        file = MigrationFile(
            name="myscript",
            path=tmp_path / "myscript.yaml",
            file_type="recipe",
            current_version=None,
        )
        temp_dir = tmp_path / "temp"
        result = adapter.get_temp_output_path(file, temp_dir)
        assert result == temp_dir / "migrations" / "myscript.yaml"

    # ME8
    def test_recipe_adapter_validate_valid_bundled_recipe(self) -> None:
        recipe_path = pkg_root() / "recipes" / "implementation.yaml"

        adapter = RecipeMigrationAdapter()
        is_valid, error = adapter.validate(recipe_path)

        assert is_valid is True
        assert error == ""

    # ME9
    def test_recipe_adapter_validate_invalid_yaml_structure(self, tmp_path: Path) -> None:
        recipe_path = tmp_path / "broken.yaml"
        recipe_path.write_text("steps: 'not_a_dict'\ningredients: 42\n")

        adapter = RecipeMigrationAdapter()
        is_valid, error = adapter.validate(recipe_path)

        assert is_valid is False
        assert len(error) > 0
        assert (
            "dict" in error.lower() or "expected" in error.lower() or "attribute" in error.lower()
        )

    # ME9b
    def test_recipe_adapter_validate_errors_non_empty_branch(self, tmp_path: Path) -> None:
        recipe_path = tmp_path / "no-kitchen-rules.yaml"
        recipe_path.write_text(
            textwrap.dedent("""\
                name: bad-recipe
                steps:
                  step1:
                    tool: run_skill
                    with:
                      skill_command: "/foo"
                    on_success: step1
            """)
        )

        adapter = RecipeMigrationAdapter()
        is_valid, error = adapter.validate(recipe_path)

        assert is_valid is False
        assert len(error) > 0
        assert "kitchen_rules" in error.lower()


# ---------------------------------------------------------------------------
# ContractMigrationAdapter tests (ME10–ME14)
# ---------------------------------------------------------------------------


class TestContractMigrationAdapter:
    # ME10
    def test_contract_adapter_discover_finds_contracts(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / ".autoskillit" / "recipes" / "contracts"
        contracts_dir.mkdir(parents=True)
        (contracts_dir / "foo.yaml").write_text("skill_hashes: {}")
        (contracts_dir / "bar.yaml").write_text("skill_hashes: {}")

        adapter = ContractMigrationAdapter()
        files = adapter.discover(tmp_path)

        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"foo", "bar"}
        assert all(f.file_type == "contract" for f in files)
        assert all(f.current_version is None for f in files)

    # ME11
    def test_contract_adapter_discover_empty_dir(self, tmp_path: Path) -> None:
        adapter = ContractMigrationAdapter()
        files = adapter.discover(tmp_path)
        assert files == []

    # ME12
    def test_contract_adapter_needs_migration_stale_contract_on_disk(self, tmp_path: Path) -> None:
        """ME12: needs_migration returns True for an on-disk contract with empty skill_hashes."""
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        contracts_dir = recipes_dir / "contracts"
        contracts_dir.mkdir(parents=True)
        # A contract with empty skill_hashes is stale because bundled skills have real hashes.
        contract_path = contracts_dir / "test.yaml"
        contract_path.write_text("skill_hashes: {}\nbundled_manifest_version: '0.0.1'\n")

        file = MigrationFile(
            name="test", path=contract_path, file_type="contract", current_version=None
        )
        adapter = ContractMigrationAdapter()
        # Should be True: stale contract or load_recipe_card returns None → needs migration
        assert adapter.needs_migration(file) is True

    # ME13
    @pytest.mark.anyio
    async def test_contract_adapter_migrate_regenerates_card_on_disk(self, tmp_path: Path) -> None:
        """ME13: migrate() runs generate_recipe_card and writes a contract file to disk."""
        import shutil

        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        contracts_dir = recipes_dir / "contracts"
        contracts_dir.mkdir(parents=True)

        # Copy the project-local smoke-test recipe so generate_recipe_card has valid input
        src_recipe = PROJECT_ROOT / ".autoskillit" / "recipes" / "smoke-test.yaml"
        assert src_recipe.exists(), f"smoke-test source missing: {src_recipe}"
        shutil.copy2(src_recipe, recipes_dir / "smoke-test.yaml")

        contract_path = contracts_dir / "smoke-test.yaml"
        contract_path.write_text("skill_hashes: {}\n")  # stale placeholder

        file = MigrationFile(
            name="smoke-test", path=contract_path, file_type="contract", current_version=None
        )
        adapter = ContractMigrationAdapter()
        result = await adapter.migrate(file, temp_dir=tmp_path / "temp")

        assert result.success is True
        assert result.name == "smoke-test"
        # generate_recipe_card writes a real contract file; verify it exists and is non-trivial
        written = contract_path.read_text()
        assert "skill_hashes" in written

    # ME14
    @pytest.mark.anyio
    async def test_contract_adapter_migrate_fails_gracefully_when_no_source(
        self, tmp_path: Path
    ) -> None:
        contracts_dir = tmp_path / ".autoskillit" / "recipes" / "contracts"
        contracts_dir.mkdir(parents=True)
        contract_path = contracts_dir / "missing.yaml"
        contract_path.write_text("skill_hashes: {}")
        # recipes_dir / "missing.yaml" does NOT exist

        file = MigrationFile(
            name="missing", path=contract_path, file_type="contract", current_version=None
        )
        adapter = ContractMigrationAdapter()
        result = await adapter.migrate(file, temp_dir=tmp_path / "temp")

        assert result.success is False
        assert "not found" in (result.error or "")


# ---------------------------------------------------------------------------
# MigrationEngine tests (ME15–ME21)
# ---------------------------------------------------------------------------


class TestMigrationEngine:
    # ME15
    def test_engine_get_adapter_returns_correct_type(self) -> None:
        engine = default_migration_engine()
        assert isinstance(engine.get_adapter("recipe"), RecipeMigrationAdapter)
        assert isinstance(engine.get_adapter("contract"), ContractMigrationAdapter)

    # ME16
    def test_engine_get_adapter_returns_none_for_unknown(self) -> None:
        engine = default_migration_engine()
        assert engine.get_adapter("unknown") is None

    # ME17
    @pytest.mark.anyio
    async def test_engine_skips_migration_when_not_needed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "autoskillit.migration.engine.applicable_migrations",
            lambda *a, **kw: [],
        )
        mock_headless = AsyncMock()
        file = MigrationFile(
            name="test", path=tmp_path / "test.yaml", file_type="recipe", current_version="99.0.0"
        )
        engine = default_migration_engine()
        result = await engine.migrate_file(
            file, run_headless=mock_headless, temp_dir=tmp_path / "temp"
        )

        assert result.success is True
        assert result.name == "test"
        mock_headless.assert_not_awaited()

    # ME18
    @pytest.mark.anyio
    async def test_engine_writes_back_on_successful_headless_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recipe_path = tmp_path / ".autoskillit" / "recipes" / "mypipe.yaml"
        recipe_path.parent.mkdir(parents=True)
        original_content = "name: mypipe\n"
        recipe_path.write_text(original_content)

        new_content = "name: mypipe\n# migrated\nautoskillit_version: '1.0.0'\n"
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_out = temp_dir / "migrations" / "mypipe.yaml"
        temp_out.parent.mkdir(parents=True)
        temp_out.write_text(new_content)

        monkeypatch.setattr(
            "autoskillit.migration.engine.applicable_migrations",
            lambda *a, **kw: [_make_migration_note()],
        )
        mock_headless = AsyncMock(return_value=_make_skill_result(True))

        file = MigrationFile(
            name="mypipe", path=recipe_path, file_type="recipe", current_version="0.0.1"
        )
        engine = default_migration_engine()
        result = await engine.migrate_file(file, run_headless=mock_headless, temp_dir=temp_dir)

        assert result.success is True
        assert recipe_path.read_text() == new_content
        mock_headless.assert_awaited_once()

    # ME19
    @pytest.mark.anyio
    async def test_engine_returns_failure_when_headless_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recipe_path = tmp_path / ".autoskillit" / "recipes" / "test.yaml"
        recipe_path.parent.mkdir(parents=True)
        recipe_path.write_text("name: test\n")

        monkeypatch.setattr(
            "autoskillit.migration.engine.applicable_migrations",
            lambda *a, **kw: [_make_migration_note()],
        )
        mock_headless = AsyncMock(
            return_value=_make_skill_result(False, "headless session failed")
        )

        file = MigrationFile(
            name="test", path=recipe_path, file_type="recipe", current_version="0.0.1"
        )
        engine = default_migration_engine()
        result = await engine.migrate_file(
            file, run_headless=mock_headless, temp_dir=tmp_path / "temp"
        )

        assert result.success is False
        assert "headless session failed" in (result.error or "")
        mock_headless.assert_awaited_once()

    # ME20
    @pytest.mark.anyio
    async def test_engine_returns_failure_when_temp_output_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recipe_path = tmp_path / ".autoskillit" / "recipes" / "test.yaml"
        recipe_path.parent.mkdir(parents=True)
        recipe_path.write_text("name: test\n")
        # temp output file intentionally NOT created

        monkeypatch.setattr(
            "autoskillit.migration.engine.applicable_migrations",
            lambda *a, **kw: [_make_migration_note()],
        )
        mock_headless = AsyncMock(return_value=_make_skill_result(True))

        file = MigrationFile(
            name="test", path=recipe_path, file_type="recipe", current_version="0.0.1"
        )
        engine = default_migration_engine()
        result = await engine.migrate_file(
            file, run_headless=mock_headless, temp_dir=tmp_path / "temp"
        )

        assert result.success is False
        assert result.error is not None
        assert "output" in result.error.lower()

    # ME21
    def test_default_engine_has_both_adapters(self) -> None:
        engine = default_migration_engine()
        assert engine.get_adapter("recipe") is not None
        assert engine.get_adapter("contract") is not None


class TestAdapterHierarchy:
    # ME-ADP1
    def test_adapter_abcs_are_importable(self) -> None:
        from abc import ABC

        assert issubclass(HeadlessMigrationAdapter, MigrationAdapter)
        assert issubclass(DeterministicMigrationAdapter, MigrationAdapter)
        assert issubclass(MigrationAdapter, ABC)

    # ME-ADP2
    def test_recipe_adapter_is_headless(self) -> None:
        assert isinstance(RecipeMigrationAdapter(), HeadlessMigrationAdapter)

    # ME-ADP3
    def test_contract_adapter_is_deterministic(self) -> None:
        assert isinstance(ContractMigrationAdapter(), DeterministicMigrationAdapter)

    # ME-ADP4
    def test_contract_migrate_has_no_run_headless_param(self) -> None:
        import inspect

        sig = inspect.signature(ContractMigrationAdapter.migrate)
        assert "run_headless" not in sig.parameters

    # ME-RT1
    def test_incomplete_adapter_raises_type_error(self) -> None:
        class BrokenAdapter(HeadlessMigrationAdapter):
            file_type = "broken"
            # missing: discover, needs_migration, validate, migrate

        with pytest.raises(TypeError):
            BrokenAdapter()


# ---------------------------------------------------------------------------
# DiagramMigrationAdapter tests (DG-16 through DG-20)
# ---------------------------------------------------------------------------

_SAMPLE_RECIPE_YAML_FOR_DIAG = """\
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
    path = tmp_path / "my-recipe.yaml"
    path.write_text(_SAMPLE_RECIPE_YAML_FOR_DIAG)
    return path


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

    # DG-19
    @pytest.mark.anyio
    async def test_diagram_adapter_migrate_writes_file(
        self, tmp_path: Path, sample_recipe_yaml_for_diagram: Path
    ) -> None:
        """DG-19: DiagramMigrationAdapter.migrate() writes diagram file."""
        import shutil

        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        diagrams_dir = recipes_dir / "diagrams"
        diagrams_dir.mkdir(parents=True)
        shutil.copy2(sample_recipe_yaml_for_diagram, recipes_dir / "my-recipe.yaml")

        file = MigrationFile(
            name="my-recipe",
            path=diagrams_dir / "my-recipe.md",
            file_type="diagram",
            current_version=None,
        )
        result = await DiagramMigrationAdapter().migrate(file, temp_dir=tmp_path / "temp")
        assert result.success is True

    # DG-20
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

    def test_default_engine_includes_diagram_adapter(self) -> None:
        """default_migration_engine() registers the DiagramMigrationAdapter."""
        engine = default_migration_engine()
        assert isinstance(engine.get_adapter("diagram"), DiagramMigrationAdapter)


class TestMigrateRecipesConstant:
    def test_constant_value(self) -> None:
        assert MIGRATE_RECIPES_MAX_RETRIES == 3

    @pytest.mark.anyio
    async def test_failed_headless_retries_match_constant(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recipe_path = tmp_path / ".autoskillit" / "recipes" / "myrecipe.yaml"
        recipe_path.parent.mkdir(parents=True)
        recipe_path.write_text("name: myrecipe\n")
        monkeypatch.setattr(
            "autoskillit.migration.engine.applicable_migrations",
            lambda *a, **kw: [_make_migration_note()],
        )
        mock_rh = AsyncMock(return_value=_make_skill_result(False, "boom"))
        adapter = RecipeMigrationAdapter()
        file = MigrationFile(
            name="myrecipe",
            path=recipe_path,
            file_type="recipe",
            current_version="0.0.1",
        )
        result = await adapter.migrate(file, run_headless=mock_rh, temp_dir=tmp_path)
        assert not result.success
        assert result.retries_attempted == MIGRATE_RECIPES_MAX_RETRIES
