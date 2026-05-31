"""REQ-FILT-003: cascade entries for planner, _llm_triage, smoke_utils, version."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_filter import (
    LAYER_CASCADE_CONSERVATIVE,
    FilterMode,
    build_test_scope,
    load_manifest,
)


class TestCascadeNewEntries:
    """REQ-FILT-003: four new packages must not force a full test run."""

    @pytest.mark.parametrize(
        "filepath,mode,items_to_create,expected_in_result",
        [
            # Conservative: planner only touches its own tests + specific recipe files
            (
                "src/autoskillit/planner/__init__.py",
                FilterMode.CONSERVATIVE,
                [
                    "planner",
                    "recipe/test_rules_contracts.py",
                    "recipe/test_contracts.py",
                    "recipe/test_planner_recipe.py",
                ],
                [
                    "planner",
                    "test_rules_contracts.py",
                    "test_contracts.py",
                    "test_planner_recipe.py",
                ],
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
                "src/autoskillit/smoke_utils/__init__.py",
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
                "src/autoskillit/smoke_utils/__init__.py",
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
                "src/autoskillit/hooks/guards/quota_guard.py",
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
            if "/" in item:
                (tests_root / item).parent.mkdir(parents=True, exist_ok=True)
                (tests_root / item).touch()
            elif item.endswith(".py"):
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
    "test_tools_dispatch_validation.py",
    "test_tools_kitchen_gate_features.py",
    "test_tools_kitchen_envelope.py",
    "test_service_wrappers.py",
    "test_tools_list_recipes.py",
]

_CLI_FILE_LEVEL_ENTRIES = [
    "test_cli_prompts.py",
    "test_l3_orchestrator_prompt.py",
    "test_cook_order_picker.py",
]

_FLEET_FILE_LEVEL_ENTRIES = [
    "test_fleet_e2e.py",
    "test_campaign_capture.py",
    "test_pack_enforcement.py",
    "test_pack_enforcement_e2e.py",
    "test_dispatch_ingredient_validation.py",
    "test_dispatch_recipe_kind_gate.py",
]

_FLEET_SERVER_FILE_LEVEL_ENTRIES = [
    "test_dispatch_crash_diagnostics.py",
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
        # hooks/test_fmt_status.py is not in the file-level cascade or hook unconditionals;
        # it should be excluded. Unconditional hook files (test_hook_executability.py etc.)
        # are allowed to appear since they run regardless of trigger.
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
        result_names = {p.name for p in result}
        assert "test_fmt_status.py" not in result_names, (
            "test_fmt_status.py (not in cascade or hook unconditionals) should not appear"
        )

    def test_recipe_cascade_fleet_file_level_only(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        fleet_dir = tests_root / "fleet"
        fleet_dir.mkdir(parents=True, exist_ok=True)
        for fname in _FLEET_FILE_LEVEL_ENTRIES:
            (fleet_dir / fname).touch()
        (fleet_dir / "test_fleet_cli.py").touch()

        result = build_test_scope(
            changed_files={"src/autoskillit/recipe/schema.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert len(result) >= len(_FLEET_FILE_LEVEL_ENTRIES)
        result_names = {p.name for p in result}
        for fname in _FLEET_FILE_LEVEL_ENTRIES:
            assert fname in result_names, f"{fname!r} missing from recipe cascade result"
        assert "test_fleet_cli.py" not in result_names

    def test_recipe_layer_uses_hooks_file_not_dir(self, tmp_path: Path) -> None:
        """recipe/__init__.py change → hooks/test_recipe_contract_freshness.py in scope; hooks/ dir NOT."""
        tests_root = tmp_path / "tests"
        hooks_dir = tests_root / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        (hooks_dir / "test_recipe_contract_freshness.py").touch()
        result = build_test_scope(
            changed_files={"src/autoskillit/recipe/__init__.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        path_names = {p.name for p in result}
        assert "test_recipe_contract_freshness.py" in path_names, (
            "recipe layer must include hooks/test_recipe_contract_freshness.py"
        )
        assert "hooks" not in path_names, "recipe layer must not include the entire hooks/ dir"


_WORKSPACE_RECIPE_FILE_ENTRIES = [
    "test_contracts.py",
    "test_rules_skill_content.py",
    "test_api.py",
    "test_api_cache_isolation.py",
    "test_bem_wrapper_structure.py",
    "test_bundled_recipes_dispatch_ready.py",
    "test_bundled_recipes_general.py",
    "test_callable_contracts.py",
    "test_contracts_block_fingerprint.py",
    "test_contract_verdict_output_required.py",
    "test_deep_staleness.py",
    "test_diagnose_ci_subtype_output.py",
    "test_hidden_ingredients.py",
    "test_io_discovery.py",
    "test_issue_url_pipeline.py",
    "test_planner_contracts.py",
    "test_recipe_temp_substitution.py",
    "test_repository.py",
    "test_research_campaign.py",
    "test_rules_contracts.py",
    "test_rules_dataflow_handoff.py",
    "test_rules_skill_routing.py",
    "test_rules_skills.py",
    "test_rules_tools.py",
    "test_skill_contract_completeness.py",
    "test_skill_emit_consistency.py",
    "test_skip_guard_deferral.py",
    "test_staleness_cache.py",
    "test_sub_recipe_loading.py",
    "test_sub_recipe_validation.py",
]

_PLANNER_RECIPE_FILE_ENTRIES = [
    "test_rules_contracts.py",
    "test_contracts.py",
    "test_planner_recipe.py",
]


class TestWorkspacePlannerCascadeNarrowing:
    """REQ-FILT-003: workspace/planner cascade uses file-level recipe entries, not directory."""

    def test_workspace_cascade_recipe_file_level_only(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        recipe_dir = tests_root / "recipe"
        recipe_dir.mkdir(parents=True, exist_ok=True)
        for fname in _WORKSPACE_RECIPE_FILE_ENTRIES:
            (recipe_dir / fname).touch()
        (recipe_dir / "test_unrelated.py").touch()

        result = build_test_scope(
            changed_files={"src/autoskillit/workspace/skills.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        result_names = {p.name for p in result}
        for fname in _WORKSPACE_RECIPE_FILE_ENTRIES:
            assert fname in result_names, f"{fname!r} missing from workspace cascade result"
        assert "test_unrelated.py" not in result_names

    def test_planner_cascade_recipe_file_level_only(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        recipe_dir = tests_root / "recipe"
        recipe_dir.mkdir(parents=True, exist_ok=True)
        for fname in _PLANNER_RECIPE_FILE_ENTRIES:
            (recipe_dir / fname).touch()
        (recipe_dir / "test_unrelated.py").touch()

        result = build_test_scope(
            changed_files={"src/autoskillit/planner/validator.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        result_names = {p.name for p in result}
        for fname in _PLANNER_RECIPE_FILE_ENTRIES:
            assert fname in result_names, f"{fname!r} missing from planner cascade result"
        assert "test_unrelated.py" not in result_names

    def test_planner_cascade_no_full_recipe_directory(self, tmp_path: Path) -> None:
        """planner change must NOT include the full recipe/ directory."""
        tests_root = tmp_path / "tests"
        recipe_dir = tests_root / "recipe"
        recipe_dir.mkdir(parents=True, exist_ok=True)
        (recipe_dir / "test_planner_recipe.py").touch()

        result = build_test_scope(
            changed_files={"src/autoskillit/planner/validator.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        assert not any(
            "/recipe/" in str(p) and p.name != "test_planner_recipe.py" for p in result
        ), f"planner cascade must not include the full recipe/ directory; got {result}"

    def test_skills_extended_skill_md_recipe_file_level(self, tmp_path: Path) -> None:
        tests_root = tmp_path / "tests"
        recipe_dir = tests_root / "recipe"
        recipe_dir.mkdir(parents=True, exist_ok=True)
        (recipe_dir / "test_rules_skill_content.py").touch()
        (recipe_dir / "test_unrelated.py").touch()

        manifest = load_manifest(Path(__file__).parent.parent)
        result = build_test_scope(
            changed_files={"src/autoskillit/skills_extended/make-plan/SKILL.md"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
            manifest=manifest,
        )
        assert result is not None
        result_names = {p.name for p in result}
        assert "test_rules_skill_content.py" in result_names
        assert "test_unrelated.py" not in result_names


def test_session_log_cascade_targets_hooks_quota_check() -> None:
    """session_log cascade must point to hooks/ after test_quota_check.py was moved."""
    from tests._test_filter import MODULE_CASCADE_EXECUTION

    targets = MODULE_CASCADE_EXECUTION["session_log"]
    assert "hooks/test_quota_check.py" in targets, (
        "session_log cascade must include hooks/test_quota_check.py"
    )
    assert "infra/test_quota_check.py" not in targets, (
        "stale infra/test_quota_check.py still present in session_log cascade"
    )


class TestServerFleetCascadeNarrowing:
    """REQ-FLEET-002: server cascade targets only fleet files with server imports."""

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
            changed_files={"src/autoskillit/server/tools/tools_kitchen.py"},
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

    def test_server_source_change_includes_fleet_server_file_level_entries(
        self, tmp_path: Path
    ) -> None:
        """A server source change cascades to all fleet server file-level entries."""
        tests_root = tmp_path / "tests"
        fleet_dir = tests_root / "fleet"
        fleet_dir.mkdir(parents=True, exist_ok=True)
        for fname in _FLEET_SERVER_FILE_LEVEL_ENTRIES:
            (fleet_dir / fname).touch()
        (fleet_dir / "test_fleet.py").touch()
        server_dir = tests_root / "server"
        server_dir.mkdir(parents=True, exist_ok=True)
        (server_dir / "test_factory.py").touch()

        result = build_test_scope(
            changed_files={"src/autoskillit/server/tools/tools_kitchen.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None, "server source change should not force a full run"
        result_names = {p.name for p in result}
        for fname in _FLEET_SERVER_FILE_LEVEL_ENTRIES:
            assert fname in result_names, f"fleet/{fname} must appear in server cascade"


_PIPELINE_EXECUTION_FILE_ENTRIES = [
    "test_backend_dispatch.py",
    "test_boundary_pty_dispatch.py",
    "test_clone_guard.py",
    "test_flush_provider_integration.py",
    "test_headless_add_dirs.py",
    "test_headless_backend_mixing.py",
    "test_headless_backend_override.py",
    "test_headless_backend_resolution.py",
    "test_headless_core.py",
    "test_headless_dispatch.py",
    "test_headless_env_injection.py",
    "test_headless_env_scrub.py",
    "test_headless_path_validation.py",
    "test_headless_provider_fallback.py",
    "test_headless_provider_forwarding.py",
    "test_headless_result.py",
    "test_headless_result_write_reconciliation.py",
    "test_headless_synthesis.py",
    "test_idle_output_env.py",
    "test_planner_write_isolation.py",
    "test_session_log_flush.py",
    "test_write_evidence.py",
    "test_zero_write_detection.py",
]


class TestPipelineCascadeNarrowing:
    """pipeline cascade targets only specific execution/ test files, not the full directory."""

    def test_pipeline_cascade_excludes_execution_directory(self) -> None:
        """The pipeline cascade must NOT include 'execution' as a bare directory entry."""
        pipeline_entries = LAYER_CASCADE_CONSERVATIVE["pipeline"]
        assert "execution" not in pipeline_entries, (
            "'execution' must not be a bare directory entry in the pipeline cascade"
        )

    def test_pipeline_cascade_includes_execution_file_entries(self) -> None:
        """The pipeline cascade must include all entries from _PIPELINE_EXECUTION_FILE_ENTRIES."""
        pipeline_entries = LAYER_CASCADE_CONSERVATIVE["pipeline"]
        execution_entries = [e for e in pipeline_entries if e.startswith("execution/")]
        assert len(execution_entries) >= len(_PIPELINE_EXECUTION_FILE_ENTRIES), (
            f"pipeline cascade must include >={len(_PIPELINE_EXECUTION_FILE_ENTRIES)} execution/ file-level entries, "
            f"got {len(execution_entries)}"
        )

    def test_pipeline_cascade_preserves_pipeline_directory(self) -> None:
        """The pipeline cascade must preserve 'pipeline' as a bare directory entry,
        maintaining the AGGRESSIVE <= CONSERVATIVE invariant."""
        pipeline_entries = LAYER_CASCADE_CONSERVATIVE["pipeline"]
        assert "pipeline" in pipeline_entries, (
            "'pipeline' directory must remain in the pipeline cascade "
            "to preserve the AGGRESSIVE <= CONSERVATIVE invariant"
        )

    def test_pipeline_change_selects_file_not_dir(self, tmp_path: Path) -> None:
        """Changing a pipeline source file selects specific execution/ files, not the directory."""
        tests_root = tmp_path / "tests"
        execution_dir = tests_root / "execution"
        execution_dir.mkdir(parents=True, exist_ok=True)
        for fname in _PIPELINE_EXECUTION_FILE_ENTRIES:
            (execution_dir / fname).touch()
        pipeline_dir = tests_root / "pipeline"
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        (pipeline_dir / "test_gate.py").touch()

        result = build_test_scope(
            changed_files={"src/autoskillit/pipeline/gate.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None, "pipeline source change should not force a full run"
        result_names = {p.name for p in result}
        assert "execution" not in result_names, (
            "'execution' directory must NOT appear in pipeline cascade result"
        )
        for fname in _PIPELINE_EXECUTION_FILE_ENTRIES[:5]:
            assert fname in result_names, (
                f"execution/{fname} must appear in pipeline cascade result"
            )
