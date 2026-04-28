"""REQ-FILT-003: cascade entries for planner, _llm_triage, smoke_utils, version."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_filter import FilterMode, build_test_scope


class TestCascadeNewEntries:
    """REQ-FILT-003: four new packages must not force a full test run."""

    @pytest.mark.parametrize(
        "filepath,mode,items_to_create,expected_in_result",
        [
            # Conservative: planner only touches its own tests
            (
                "src/autoskillit/planner/__init__.py",
                FilterMode.CONSERVATIVE,
                ["planner"],
                ["planner"],
            ),
            # Conservative: _llm_triage cascades into server + direct test file
            (
                "src/autoskillit/_llm_triage.py",
                FilterMode.CONSERVATIVE,
                ["server", "test_llm_triage.py"],
                ["server", "test_llm_triage.py"],
            ),
            # Conservative: smoke_utils cascades into recipe + direct test file
            (
                "src/autoskillit/smoke_utils.py",
                FilterMode.CONSERVATIVE,
                ["recipe", "test_smoke_utils.py"],
                ["recipe", "test_smoke_utils.py"],
            ),
            # Conservative: version cascades into server + direct test file
            (
                "src/autoskillit/version.py",
                FilterMode.CONSERVATIVE,
                ["server", "test_version.py"],
                ["server", "test_version.py"],
            ),
            # Aggressive: planner scoped to its own tests directory
            (
                "src/autoskillit/planner/__init__.py",
                FilterMode.AGGRESSIVE,
                ["planner"],
                ["planner"],
            ),
            # Aggressive: _llm_triage scoped to its direct test file
            (
                "src/autoskillit/_llm_triage.py",
                FilterMode.AGGRESSIVE,
                ["test_llm_triage.py"],
                ["test_llm_triage.py"],
            ),
            # Aggressive: smoke_utils scoped to its direct test file
            (
                "src/autoskillit/smoke_utils.py",
                FilterMode.AGGRESSIVE,
                ["test_smoke_utils.py"],
                ["test_smoke_utils.py"],
            ),
            # Aggressive: version scoped to its direct test file
            (
                "src/autoskillit/version.py",
                FilterMode.AGGRESSIVE,
                ["test_version.py"],
                ["test_version.py"],
            ),
            # Conservative: quota_guard.py cascades into hooks tests +
            # execution/test_quota_sleep.py
            (
                "src/autoskillit/hooks/quota_guard.py",
                FilterMode.CONSERVATIVE,
                ["hooks", "execution", "execution/test_quota_sleep.py"],
                ["hooks", "test_quota_sleep.py"],
            ),
        ],
    )
    def test_cascade_new_entries_not_full_run(
        self,
        tmp_path: Path,
        filepath: str,
        mode: FilterMode,
        items_to_create: list[str],
        expected_in_result: list[str],
    ) -> None:
        tests_root = tmp_path / "tests"
        tests_root.mkdir(parents=True, exist_ok=True)
        for item in items_to_create:
            if item.endswith(".py"):
                (tests_root / item).touch()
            else:
                (tests_root / item).mkdir(parents=True, exist_ok=True)

        result = build_test_scope(
            changed_files={filepath},
            mode=mode,
            tests_root=tests_root,
        )
        assert result is not None, (
            f"{filepath} still forces a full test run in {mode} mode — "
            "cascade entry is missing from LAYER_CASCADE_"
            + ("CONSERVATIVE" if mode == FilterMode.CONSERVATIVE else "AGGRESSIVE")
        )
        result_names = {p.name for p in result}
        for expected in expected_in_result:
            assert expected in result_names, (
                f"{expected!r} not found in result scope {result_names!r} "
                f"for {filepath} in {mode} mode"
            )


_SERVER_FILE_LEVEL_ENTRIES = [
    "test_factory.py",
    "test_tools_load_recipe.py",
    "test_server_tool_registration.py",
    "test_mcp_overrides.py",
    "test_smoke_pipeline.py",
    "test_tools_dispatch.py",
    "test_tools_kitchen_gate.py",
    "test_tools_kitchen_envelope.py",
    "test_service_wrappers.py",
    "test_tools_list_recipes.py",
]

_CLI_FILE_LEVEL_ENTRIES = [
    "test_cli_prompts.py",
    "test_l3_orchestrator_prompt.py",
    "test_cook_order_command.py",
    "test_cook_order_picker.py",
]


class TestRecipeCascadeNarrowing:
    """REQ-RECIPE-001/002/003: recipe cascade uses file-level entries for server/cli,
    and uses file-level entries for migration/hooks (not full-directory entries)."""

    def test_recipe_cascade_server_file_level_only(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        server_dir = tests_root / "server"
        server_dir.mkdir(parents=True, exist_ok=True)
        for fname in _SERVER_FILE_LEVEL_ENTRIES:
            (server_dir / fname).touch()
        (server_dir / "test_serve_guard.py").touch()

        result = build_test_scope(
            changed_files={"src/autoskillit/recipe/__init__.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        result_names = {p.name for p in result}
        for fname in _SERVER_FILE_LEVEL_ENTRIES:
            assert fname in result_names, f"{fname!r} missing from recipe cascade result"
        assert "test_serve_guard.py" not in result_names

    def test_recipe_cascade_cli_file_level_only(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        cli_dir = tests_root / "cli"
        cli_dir.mkdir(parents=True, exist_ok=True)
        for fname in _CLI_FILE_LEVEL_ENTRIES:
            (cli_dir / fname).touch()
        (cli_dir / "test_app.py").touch()

        result = build_test_scope(
            changed_files={"src/autoskillit/recipe/schema.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        result_names = {p.name for p in result}
        for fname in _CLI_FILE_LEVEL_ENTRIES:
            assert fname in result_names, f"{fname!r} missing from recipe cascade result"
        assert "test_app.py" not in result_names

    def test_recipe_cascade_no_migration_full_directory(self, tmp_path: Path) -> None:
        # migration/test_store.py is not in the file-level cascade; it should be excluded.
        tests_root = tmp_path / "tests"
        migration_dir = tests_root / "migration"
        migration_dir.mkdir(parents=True, exist_ok=True)
        (migration_dir / "test_store.py").touch()

        result = build_test_scope(
            changed_files={"src/autoskillit/recipe/schema.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        assert not any("/migration/" in str(p) for p in result), (
            f"migration/test_store.py (not in cascade) should not appear; got {result}"
        )

    def test_recipe_cascade_no_hooks_full_directory(self, tmp_path: Path) -> None:
        # hooks/test_fmt_status.py is not in the file-level cascade; it should be excluded.
        tests_root = tmp_path / "tests"
        hooks_dir = tests_root / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        (hooks_dir / "test_fmt_status.py").touch()

        result = build_test_scope(
            changed_files={"src/autoskillit/recipe/schema.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        assert not any("/hooks/" in str(p) for p in result), (
            f"hooks/test_fmt_status.py (not in cascade) should not appear; got {result}"
        )


class TestServerFleetCascadeNarrowing:
    """REQ-FLEET-002: server cascade targets only fleet/test_pack_enforcement.py."""

    def test_server_source_change_targets_pack_enforcement_only(self, tmp_path: Path) -> None:
        """A server source change cascades to fleet/test_pack_enforcement.py only,
        not to other fleet test files."""
        tests_root = tmp_path / "tests"
        fleet_dir = tests_root / "fleet"
        fleet_dir.mkdir(parents=True, exist_ok=True)
        (fleet_dir / "test_pack_enforcement.py").touch()
        (fleet_dir / "test_fleet.py").touch()
        server_dir = tests_root / "server"
        server_dir.mkdir(parents=True, exist_ok=True)
        (server_dir / "test_factory.py").touch()

        result = build_test_scope(
            changed_files={"src/autoskillit/server/tools_kitchen.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None, "server source change should not force a full run"
        result_names = {p.name for p in result}
        assert "test_pack_enforcement.py" in result_names, (
            "fleet/test_pack_enforcement.py must appear in server cascade"
        )
        assert "test_fleet.py" not in result_names, (
            "fleet/test_fleet.py must NOT appear in server cascade — "
            "only test_pack_enforcement.py has server imports"
        )
