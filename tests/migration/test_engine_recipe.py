"""Tests for the RecipeMigrationAdapter."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import autoskillit.migration.adapters_recipe as _patch_migration_adapters_recipe
from autoskillit.core.paths import pkg_root
from autoskillit.migration.adapters_recipe import RecipeMigrationAdapter
from autoskillit.migration.engine import MigrationFile
from tests._helpers import MINIMAL_VALID_RECIPE_YAML

from .conftest import make_migration_note, make_skill_result

pytestmark = [pytest.mark.layer("migration"), pytest.mark.small]


def _stamped(content: str, version: str = "1.0.0") -> str:
    return f"autoskillit_version: '{version}'\n{content}"


def _migration_file(tmp_path: Path, content: str) -> MigrationFile:
    recipe_path = tmp_path / ".autoskillit" / "recipes" / "myrecipe.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(content)
    return MigrationFile(
        name="myrecipe",
        path=recipe_path,
        file_type="recipe",
        current_version="0.0.1",
    )


def _runner_writing(path: Path, content: str) -> AsyncMock:
    async def _write_output(**_kwargs: object):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return make_skill_result(True)

    return AsyncMock(side_effect=_write_output)


def _enable_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _patch_migration_adapters_recipe,
        "applicable_migrations",
        lambda *a, **kw: [make_migration_note()],
    )


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
            _patch_migration_adapters_recipe,
            "applicable_migrations",
            lambda *a, **kw: [make_migration_note()],
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
            _patch_migration_adapters_recipe,
            "applicable_migrations",
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
            _patch_migration_adapters_recipe,
            "applicable_migrations",
            lambda *a, **kw: [make_migration_note()],
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

        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_out = temp_dir / "migrations" / "myrecipe.yaml"

        monkeypatch.setattr(
            _patch_migration_adapters_recipe,
            "applicable_migrations",
            lambda *a, **kw: [make_migration_note()],
        )
        mock_headless = _runner_writing(temp_out, _stamped(MINIMAL_VALID_RECIPE_YAML))

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
        recipe_path.write_text("steps: 'not_a_dict'\n")

        adapter = RecipeMigrationAdapter()
        is_valid, error = adapter.validate(recipe_path)

        assert is_valid is False
        assert len(error) > 0
        assert "mapping" in error.lower() or "expected" in error.lower()

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


class TestRecipeMigrationValidation:
    @pytest.mark.anyio
    async def test_failed_headless_session_is_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recipe_path = tmp_path / ".autoskillit" / "recipes" / "myrecipe.yaml"
        recipe_path.parent.mkdir(parents=True)
        recipe_path.write_text("name: myrecipe\n")
        monkeypatch.setattr(
            _patch_migration_adapters_recipe,
            "applicable_migrations",
            lambda *a, **kw: [make_migration_note()],
        )
        mock_rh = AsyncMock(return_value=make_skill_result(False, "boom"))
        adapter = RecipeMigrationAdapter()
        file = MigrationFile(
            name="myrecipe",
            path=recipe_path,
            file_type="recipe",
            current_version="0.0.1",
        )
        result = await adapter.migrate(file, run_headless=mock_rh, temp_dir=tmp_path)
        assert not result.success
        assert result.retries_attempted == 0
        mock_rh.assert_awaited_once()

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("candidate", "expected"),
        [
            (
                _stamped(
                    MINIMAL_VALID_RECIPE_YAML.replace(
                        "kitchen_rules:\n  - Use the included stop step sentinel protocol.",
                        "kitchen_rules: []",
                    )
                ),
                "kitchen_rules",
            ),
            (
                _stamped(
                    MINIMAL_VALID_RECIPE_YAML.replace(
                        "  done:\n",
                        "  bad:\n"
                        "    tool: run_skill\n"
                        "    with:\n"
                        "      skill_command: /autoskillit:project-local\n"
                        "      cwd: .\n"
                        "    on_success: done\n"
                        "  done:\n",
                    )
                ),
                "project-local",
            ),
            (
                _stamped(
                    MINIMAL_VALID_RECIPE_YAML.replace(
                        "  done:\n",
                        "  bad:\n"
                        "    tool: no_such_tool\n"
                        "    with:\n"
                        "      bogus: value\n"
                        "    on_success: done\n"
                        "  done:\n",
                    )
                ),
                "no_such_tool",
            ),
        ],
        ids=["structural", "semantic", "binding"],
    )
    async def test_migrate_rejects_errors_introduced_by_the_migration(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        candidate: str,
        expected: str,
    ) -> None:
        _enable_migration(monkeypatch)
        adapter = RecipeMigrationAdapter()
        file = _migration_file(tmp_path, _stamped(MINIMAL_VALID_RECIPE_YAML, "0.0.1"))
        temp_dir = tmp_path / "temp"
        runner = _runner_writing(adapter.get_temp_output_path(file, temp_dir), candidate)

        result = await adapter.migrate(file, run_headless=runner, temp_dir=temp_dir)

        assert not result.success
        assert expected in (result.error or "")
        runner.assert_awaited_once()

    @pytest.mark.anyio
    async def test_migrate_tolerates_pre_existing_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable_migration(monkeypatch)
        invalid = MINIMAL_VALID_RECIPE_YAML.replace(
            "  done:\n",
            "  local:\n"
            "    tool: run_skill\n"
            "    with:\n"
            "      skill_command: /autoskillit:project-local\n"
            "      cwd: .\n"
            "    on_success: done\n"
            "  done:\n",
        )
        adapter = RecipeMigrationAdapter()
        file = _migration_file(tmp_path, _stamped(invalid, "0.0.1"))
        temp_dir = tmp_path / "temp"
        runner = _runner_writing(adapter.get_temp_output_path(file, temp_dir), _stamped(invalid))

        result = await adapter.migrate(file, run_headless=runner, temp_dir=temp_dir)

        assert result.success

    @pytest.mark.anyio
    async def test_migrate_rejects_new_errors_alongside_baseline_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable_migration(monkeypatch)
        baseline = MINIMAL_VALID_RECIPE_YAML.replace(
            "kitchen_rules:\n  - Use the included stop step sentinel protocol.",
            "kitchen_rules: []",
        )
        candidate = baseline.replace(
            "  done:\n",
            "  bad:\n"
            "    tool: no_such_tool\n"
            "    with:\n"
            "      bogus: value\n"
            "    on_success: done\n"
            "  done:\n",
        )
        adapter = RecipeMigrationAdapter()
        file = _migration_file(tmp_path, _stamped(baseline, "0.0.1"))
        temp_dir = tmp_path / "temp"
        runner = _runner_writing(adapter.get_temp_output_path(file, temp_dir), _stamped(candidate))

        result = await adapter.migrate(file, run_headless=runner, temp_dir=temp_dir)

        assert not result.success
        assert "no_such_tool" in (result.error or "")

    @pytest.mark.anyio
    async def test_migrate_keeps_distinct_findings_from_same_rule_and_step(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable_migration(monkeypatch)
        original = """\
name: myrecipe
description: Capture fixture.
kitchen_rules: [Follow the stop protocol.]
ingredients:
  plan:
    description: Plan path.
    required: true
    default: /tmp/plan.md
steps:
  implement:
    tool: run_skill
    with:
      skill_command: /autoskillit:implement-worktree-no-merge ${{ inputs.plan }}
      cwd: .
    capture:
      first_bad: ${{ result.first_bad }}
    on_success: done
    on_failure: done
  done:
    action: stop
    message: Done.
"""
        candidate = original.replace(
            "      first_bad: ${{ result.first_bad }}\n",
            "      first_bad: ${{ result.first_bad }}\n"
            "      second_bad: ${{ result.second_bad }}\n",
        )
        adapter = RecipeMigrationAdapter()
        file = _migration_file(tmp_path, _stamped(original, "0.0.1"))
        temp_dir = tmp_path / "temp"
        runner = _runner_writing(adapter.get_temp_output_path(file, temp_dir), _stamped(candidate))

        result = await adapter.migrate(file, run_headless=runner, temp_dir=temp_dir)

        assert not result.success
        assert "second_bad" in (result.error or "")

    @pytest.mark.anyio
    async def test_migrate_uses_original_contract_card_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable_migration(monkeypatch)
        adapter = RecipeMigrationAdapter()
        file = _migration_file(tmp_path, _stamped(MINIMAL_VALID_RECIPE_YAML, "0.0.1"))
        source_contracts = file.path.parent / "contracts"
        source_contracts.mkdir()
        (source_contracts / "myrecipe.yaml").write_text(
            "dataflow:\n  - step: original-card\n    available: []\n    required: [source_only]\n"
        )
        temp_dir = tmp_path / "temp"
        temp_out = adapter.get_temp_output_path(file, temp_dir)
        temp_contracts = temp_out.parent / "contracts"
        temp_contracts.mkdir(parents=True)
        (temp_contracts / "myrecipe.yaml").write_text(
            "dataflow:\n  - step: temp-card\n    available: []\n    required: [temp_only]\n"
        )
        runner = _runner_writing(temp_out, _stamped(MINIMAL_VALID_RECIPE_YAML))

        result = await adapter.migrate(file, run_headless=runner, temp_dir=temp_dir)

        assert result.success

    @pytest.mark.anyio
    @pytest.mark.parametrize("version", [None, "0.9.0"], ids=["missing", "old"])
    async def test_migrate_rejects_unstamped_output(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        version: str | None,
    ) -> None:
        _enable_migration(monkeypatch)
        adapter = RecipeMigrationAdapter()
        file = _migration_file(tmp_path, _stamped(MINIMAL_VALID_RECIPE_YAML, "0.0.1"))
        temp_dir = tmp_path / "temp"
        candidate = (
            MINIMAL_VALID_RECIPE_YAML
            if version is None
            else _stamped(MINIMAL_VALID_RECIPE_YAML, version)
        )
        runner = _runner_writing(adapter.get_temp_output_path(file, temp_dir), candidate)

        result = await adapter.migrate(file, run_headless=runner, temp_dir=temp_dir)

        assert not result.success
        assert "autoskillit_version 1.0.0" in (result.error or "")

    @pytest.mark.anyio
    async def test_migrate_does_not_reuse_stale_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable_migration(monkeypatch)
        adapter = RecipeMigrationAdapter()
        file = _migration_file(tmp_path, _stamped(MINIMAL_VALID_RECIPE_YAML, "0.0.1"))
        temp_dir = tmp_path / "temp"
        temp_out = adapter.get_temp_output_path(file, temp_dir)
        temp_out.parent.mkdir(parents=True)
        temp_out.write_text(_stamped(MINIMAL_VALID_RECIPE_YAML))
        runner = AsyncMock(return_value=make_skill_result(True))

        result = await adapter.migrate(file, run_headless=runner, temp_dir=temp_dir)

        assert not result.success
        assert "did not produce output" in (result.error or "")

    @pytest.mark.anyio
    async def test_migrate_survives_validator_exceptions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable_migration(monkeypatch)
        adapter = RecipeMigrationAdapter()
        file = _migration_file(tmp_path, _stamped(MINIMAL_VALID_RECIPE_YAML, "0.0.1"))
        temp_dir = tmp_path / "temp"
        malformed = _stamped("name: x\nsteps:\n  a:\n    tool: run_cmd\n    with: hello\n")
        runner = _runner_writing(adapter.get_temp_output_path(file, temp_dir), malformed)

        result = await adapter.migrate(file, run_headless=runner, temp_dir=temp_dir)

        assert not result.success
        assert "TypeError" in (result.error or "")

    @pytest.mark.anyio
    @pytest.mark.parametrize("candidate_valid", [True, False])
    async def test_baseline_validator_exception_requires_valid_output(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        candidate_valid: bool,
    ) -> None:
        _enable_migration(monkeypatch)
        malformed = (
            "autoskillit_version: '0.0.1'\n"
            "name: x\nsteps:\n  a:\n    tool: run_cmd\n    with: hello\n"
        )
        adapter = RecipeMigrationAdapter()
        file = _migration_file(tmp_path, malformed)
        temp_dir = tmp_path / "temp"
        candidate = (
            _stamped(MINIMAL_VALID_RECIPE_YAML)
            if candidate_valid
            else malformed.replace("0.0.1", "1.0.0")
        )
        runner = _runner_writing(adapter.get_temp_output_path(file, temp_dir), candidate)

        result = await adapter.migrate(file, run_headless=runner, temp_dir=temp_dir)

        assert result.success is candidate_valid
        runner.assert_awaited_once()

    def test_recipe_adapter_validate_runs_full_validation(self, tmp_path: Path) -> None:
        recipe_path = tmp_path / "semantic-invalid.yaml"
        recipe_path.write_text(
            MINIMAL_VALID_RECIPE_YAML.replace(
                "  done:\n",
                "  bad:\n"
                "    tool: no_such_tool\n"
                "    with:\n"
                "      bogus: value\n"
                "    on_success: done\n"
                "  done:\n",
            )
        )

        valid, message = RecipeMigrationAdapter().validate(recipe_path)

        assert not valid
        assert message

    @pytest.mark.anyio
    async def test_adapter_passes_temp_display_to_both_validations(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable_migration(monkeypatch)
        adapter = RecipeMigrationAdapter(temp_dir_override="custom-temp")
        file = _migration_file(tmp_path, _stamped(MINIMAL_VALID_RECIPE_YAML, "0.0.1"))
        temp_dir = tmp_path / "custom-temp"
        runner = _runner_writing(
            adapter.get_temp_output_path(file, temp_dir), _stamped(MINIMAL_VALID_RECIPE_YAML)
        )
        completed = {
            "valid": True,
            "errors": [],
            "quality": {},
            "findings": [],
            "contracts": [],
            "binding_projection_failures": [],
        }

        with patch("autoskillit.recipe.validate_from_path", return_value=completed) as validate:
            result = await adapter.migrate(file, run_headless=runner, temp_dir=temp_dir)

        assert result.success
        assert validate.call_count == 2
        assert all(
            call.kwargs["temp_dir_relpath"] == "custom-temp" for call in validate.call_args_list
        )
        assert validate.call_args_list[0].kwargs["contract_source_path"] is None
        assert validate.call_args_list[1].kwargs["contract_source_path"] == file.path
