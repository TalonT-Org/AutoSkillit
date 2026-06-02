"""REQ-CORE-001..004: module-level cascade map for core/ submodules."""

from __future__ import annotations

from pathlib import Path

import pytest

import tests._test_filter as tf_mod
from tests._test_filter import (
    _CORE_UNIVERSAL_MODULES,
    MODULE_CASCADE_CORE,
    FilterMode,
    build_test_scope,
)

pytestmark = [pytest.mark.medium]


class TestCoreUniversalModules:
    """REQ-CORE-001: _CORE_UNIVERSAL_MODULES must exist and contain the right stems."""

    def test_required_stems_present(self) -> None:
        required = {
            "io",
            "logging",
            "types",
            "_type_constants",
            "_type_protocols_logging",
            "_type_protocols_execution",
            "_type_protocols_github",
            "_type_protocols_recipe",
            "_type_protocols_infra",
            "_type_enums",
            "_type_subprocess",
        }
        assert required <= _CORE_UNIVERSAL_MODULES

    def test_paths_and_init_not_in_universal(self) -> None:
        # __init__ handled separately by stem == "__init__" check, not via this frozenset
        assert "__init__" not in _CORE_UNIVERSAL_MODULES


class TestModuleCascadeCore:
    """REQ-CORE-002: MODULE_CASCADE_CORE must exist with validated consumer sets."""

    def test_all_values_are_frozensets(self) -> None:
        for stem, consumers in MODULE_CASCADE_CORE.items():
            assert isinstance(consumers, frozenset), f"{stem} value must be frozenset"

    def test_all_consumers_include_core(self) -> None:
        # Every narrow module's cascade must include 'core' (its own tests)
        for stem, consumers in MODULE_CASCADE_CORE.items():
            assert "core" in consumers, f"{stem} cascade missing 'core'"

    def test_feature_flags_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["feature_flags"] == frozenset(
            {"core", "cli", "config", "execution", "recipe", "server", "workspace"}
        )

    def test_branch_guard_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["branch_guard"] == frozenset(
            {"core", "pipeline", "server", "workspace"}
        )

    def test_no_universal_stem_in_map(self) -> None:
        # Universal modules must not appear in MODULE_CASCADE_CORE
        overlap = _CORE_UNIVERSAL_MODULES & set(MODULE_CASCADE_CORE.keys())
        assert not overlap, f"Universal modules in MODULE_CASCADE_CORE: {overlap}"

    def test_all_entries_present(self) -> None:
        expected_stems = {
            "_json",
            "feature_flags",
            "branch_guard",
            "_plugin_ids",
            "_terminal_table",
            "_plugin_cache",
            "github_url",
            "paths",
            "_claude_env",
            "_cmd_runner",
            "_version_snapshot",
            "claude_conventions",
            "_type_resume",
            "_type_helpers",
            "_type_protocols_workspace",
            "_type_protocols_backend",
            "_install_detect",
            "_linux_proc",
            "_type_plugin_source",
            "kitchen_state",
            "readiness",
            "session_provenance",
            "session_registry",
            "tool_sequence_analysis",
            "_type_checkpoint",
            "_type_results",
            "_type_results_execution",
            "_type_backend",
            "_type_dispatch_identity",
            "_type_figure_spec",
            "_type_session_env",
            "_type_capture",
            "_type_inspector",
            "_type_token",
            "_type_constants_env",
            "_type_constants_features",
            "_type_constants_registries",
            "_type_exceptions",
            "_step_context",
            "_execution_marker",
            "git_remote",
            "bash_write_targets",
        }
        assert set(MODULE_CASCADE_CORE.keys()) == expected_stems

    def test_type_resume_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["_type_resume"] == frozenset(
            {"core", "cli", "execution", "fleet"}
        )

    def test_type_helpers_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["_type_helpers"] == frozenset(
            {"core", "execution", "fleet", "pipeline", "recipe", "server"}
        )

    def test_type_protocols_workspace_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["_type_protocols_workspace"] == frozenset(
            {"core", "pipeline", "recipe", "workspace"}
        )

    def test_type_checkpoint_entry_exists(self) -> None:
        assert "_type_checkpoint" in MODULE_CASCADE_CORE
        assert MODULE_CASCADE_CORE["_type_checkpoint"] == frozenset(
            {"core", "execution", "fleet", "server"}
        )

    def test_type_results_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["_type_results"] == frozenset(
            {
                "core",
                "execution",
                "pipeline",
                "workspace",
                "recipe",
                "migration",
                "fleet",
                "server",
                "cli",
                "_llm_triage",
                "_test_filter",
                "hook_registry",
                "smoke_utils",
            }
        )

    def test_type_backend_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["_type_backend"] == frozenset({"core", "execution", "cli"})

    def test_type_dispatch_identity_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["_type_dispatch_identity"] == frozenset(
            {"core", "fleet", "execution"}
        )

    def test_type_figure_spec_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["_type_figure_spec"] == frozenset({"core", "report"})

    def test_type_session_env_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["_type_session_env"] == frozenset({"core", "cli"})

    def test_type_capture_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["_type_capture"] == frozenset(
            {"core", "fleet", "recipe", "cli"}
        )

    def test_type_token_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["_type_token"] == frozenset({"core", "execution"})

    def test_type_protocols_backend_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["_type_protocols_backend"] == frozenset(
            {"core", "execution", "pipeline", "cli", "workspace", "_llm_triage", "server"}
        )

    def test_json_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["_json"] == frozenset(
            {"core", "execution", "pipeline", "recipe", "server"}
        )

    def test_type_constants_env_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["_type_constants_env"] == frozenset(
            {"cli", "config", "core", "execution", "recipe", "server", "smoke_utils", "workspace"}
        )

    def test_type_constants_features_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["_type_constants_features"] == frozenset(
            {"cli", "config", "core", "fleet", "recipe", "server", "workspace"}
        )

    def test_type_constants_registries_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["_type_constants_registries"] == frozenset(
            {"cli", "config", "core", "pipeline", "recipe", "server", "workspace"}
        )

    def test_type_exceptions_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["_type_exceptions"] == frozenset(
            {"core", "fleet", "recipe", "server"}
        )

    def test_step_context_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["_step_context"] == frozenset(
            {"core", "execution", "pipeline", "server"}
        )


class TestBuildTestScopeCoreCascade:
    """REQ-CORE-003/004: build_test_scope routes core modules correctly."""

    def _make_tests_root(self, tmp_path: Path, dirs: list[str]) -> Path:
        tests_root = tmp_path / "tests"
        for d in dirs:
            (tests_root / d).mkdir(parents=True, exist_ok=True)
        return tests_root

    ALL_DIRS = [
        "core",
        "config",
        "execution",
        "pipeline",
        "workspace",
        "recipe",
        "migration",
        "fleet",
        "server",
        "cli",
        "hooks",
        "skills",
        "planner",
        "report",
        "arch",
        "contracts",
        "infra",
        "docs",
    ]

    def test_universal_module_triggers_full_cascade(self, tmp_path: Path) -> None:
        """io.py is universal → full 12-package cascade."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/io.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in [
            "core",
            "config",
            "execution",
            "pipeline",
            "workspace",
            "recipe",
            "migration",
            "server",
            "cli",
            "hooks",
            "skills",
        ]:
            assert pkg in dir_names, f"universal io.py should cascade to {pkg}"

    def test_init_triggers_full_cascade(self, tmp_path: Path) -> None:
        """__init__.py always triggers full cascade."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/__init__.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["core", "config", "execution", "server", "cli"]:
            assert pkg in dir_names

    def test_kitchen_state_narrow_cascade(self, tmp_path: Path) -> None:
        """kitchen_state.py → narrow cascade of {"core"} only."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/runtime/kitchen_state.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "core" in dir_names
        for excluded in ["config", "execution", "pipeline", "fleet", "migration", "workspace"]:
            assert excluded not in dir_names, f"narrow cascade should not include {excluded}"

    def test_unknown_core_module_fails_open_to_full_cascade(self, tmp_path: Path) -> None:
        """An unknown core module stem → full cascade (fail-open, not None)."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/_new_future_module.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        # Fail-open: should include all packages
        for pkg in ["core", "config", "execution", "server", "cli"]:
            assert pkg in dir_names, f"fail-open should include {pkg}"

    def test_aggressive_mode_unaffected(self, tmp_path: Path) -> None:
        """AGGRESSIVE mode still maps core → {core} regardless of stem."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/runtime/kitchen_state.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "core" in dir_names
        # AGGRESSIVE only maps core → core, no other dirs (except always-run)
        for excluded in [
            "execution",
            "pipeline",
            "workspace",
            "recipe",
            "migration",
            "server",
            "cli",
            "hooks",
        ]:
            assert excluded not in dir_names

    def test_paths_cascade_includes_most_packages(self, tmp_path: Path) -> None:
        """paths.py is used by almost everything → large but not full cascade."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/paths.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in [
            "core",
            "cli",
            "config",
            "execution",
            "fleet",
            "migration",
            "recipe",
            "server",
            "workspace",
        ]:
            assert pkg in dir_names

    def test_readiness_narrow_cascade(self, tmp_path: Path) -> None:
        """readiness.py → narrow cascade of {"core", "server"} only."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/runtime/readiness.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["core", "server"]:
            assert pkg in dir_names, f"narrow cascade should include {pkg}"
        for excluded in [
            "cli",
            "config",
            "execution",
            "fleet",
            "migration",
            "recipe",
            "workspace",
        ]:
            assert excluded not in dir_names, f"narrow cascade should not include {excluded}"

    def test_type_resume_narrow_routing(self, tmp_path: Path) -> None:
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/types/_type_resume.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["core", "cli", "execution", "fleet"]:
            assert pkg in dir_names, f"_type_resume cascade should include {pkg}"
        for excluded in [
            "server",
            "recipe",
            "pipeline",
            "workspace",
            "migration",
            "hooks",
        ]:
            assert excluded not in dir_names, f"_type_resume cascade should not include {excluded}"

    def test_type_helpers_narrow_routing(self, tmp_path: Path) -> None:
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/types/_type_helpers.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["core", "execution", "fleet", "pipeline", "recipe", "server"]:
            assert pkg in dir_names, f"_type_helpers cascade should include {pkg}"
        for excluded in ["cli", "hooks", "workspace", "migration"]:
            assert excluded not in dir_names, (
                f"_type_helpers cascade should not include {excluded}"
            )

    def test_type_protocols_workspace_narrow_routing(self, tmp_path: Path) -> None:
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/types/_type_protocols_workspace.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["core", "pipeline", "recipe", "workspace"]:
            assert pkg in dir_names, f"_type_protocols_workspace cascade should include {pkg}"
        for excluded in ["cli", "server", "execution", "fleet", "migration", "hooks"]:
            assert excluded not in dir_names, (
                f"_type_protocols_workspace cascade should not include {excluded}"
            )

    def test_type_checkpoint_narrow_routing(self, tmp_path: Path) -> None:
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/types/_type_checkpoint.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["core", "execution", "fleet", "server"]:
            assert pkg in dir_names, f"_type_checkpoint cascade should include {pkg}"
        for excluded in ["cli", "config", "pipeline", "workspace", "recipe", "migration", "hooks"]:
            assert excluded not in dir_names, (
                f"_type_checkpoint cascade should not include {excluded}"
            )

    def test_type_results_narrow_routing(self, tmp_path: Path) -> None:
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/types/_type_results.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["core", "execution", "server", "fleet", "migration"]:
            assert pkg in dir_names, f"_type_results cascade should include {pkg}"
        for excluded in ["config", "hooks", "skills", "planner"]:
            assert excluded not in dir_names, (
                f"_type_results cascade should not include {excluded}"
            )

    def test_type_backend_narrow_cascade(self, tmp_path: Path) -> None:
        """_type_backend → cascade of {"core", "execution"} (execution/backends imports it)."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/types/_type_backend.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "core" in dir_names
        assert "execution" in dir_names
        for excluded in ["config", "pipeline", "fleet", "migration", "workspace"]:
            assert excluded not in dir_names, f"narrow cascade should not include {excluded}"

    def test_type_capture_narrow_cascade(self, tmp_path: Path) -> None:
        """_type_capture → narrow cascade of {"core", "fleet", "recipe", "cli"}."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/types/_type_capture.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["core", "fleet", "recipe", "cli"]:
            assert pkg in dir_names, f"narrow cascade should include {pkg}"
        for excluded in ["execution", "pipeline", "hooks", "migration", "workspace"]:
            assert excluded not in dir_names, f"narrow cascade should not include {excluded}"

    def test_type_dispatch_identity_narrow_cascade(self, tmp_path: Path) -> None:
        """_type_dispatch_identity → narrow cascade of {"core", "fleet", "execution"}."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/types/_type_dispatch_identity.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["core", "fleet", "execution"]:
            assert pkg in dir_names, f"narrow cascade should include {pkg}"
        for excluded in ["config", "pipeline", "migration", "workspace", "report", "hooks"]:
            assert excluded not in dir_names, f"narrow cascade should not include {excluded}"

    def test_type_figure_spec_narrow_cascade(self, tmp_path: Path) -> None:
        """_type_figure_spec → narrow cascade of {"core", "report"}."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/types/_type_figure_spec.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["core", "report"]:
            assert pkg in dir_names, f"narrow cascade should include {pkg}"
        for excluded in ["config", "execution", "pipeline", "fleet", "migration", "workspace"]:
            assert excluded not in dir_names, f"narrow cascade should not include {excluded}"

    def test_type_session_env_narrow_cascade(self, tmp_path: Path) -> None:
        """_type_session_env → narrow cascade of {"core", "cli"}."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/types/_type_session_env.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["core", "cli"]:
            assert pkg in dir_names, f"narrow cascade should include {pkg}"
        for excluded in ["config", "execution", "pipeline", "fleet", "migration", "workspace"]:
            assert excluded not in dir_names, f"narrow cascade should not include {excluded}"

    def test_type_token_narrow_cascade(self, tmp_path: Path) -> None:
        """_type_token → narrow cascade of {"core", "execution"}."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/types/_type_token.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["core", "execution"]:
            assert pkg in dir_names, f"narrow cascade should include {pkg}"
        for excluded in ["config", "pipeline", "fleet", "migration", "workspace"]:
            assert excluded not in dir_names, f"narrow cascade should not include {excluded}"

    def test_type_protocols_backend_narrow_cascade(self, tmp_path: Path) -> None:
        """_type_protocols_backend -> cascade of
        {"core", "execution", "pipeline", "cli", "workspace", "server"} only."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/types/_type_protocols_backend.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["core", "execution", "pipeline", "cli", "workspace"]:
            assert pkg in dir_names, f"narrow cascade should include {pkg}"
        for excluded in ["config", "fleet", "migration", "recipe", "planner"]:
            assert excluded not in dir_names, f"narrow cascade should not include {excluded}"

    def test_json_narrow_cascade(self, tmp_path: Path) -> None:
        """_json -> cascade of {"core", "execution", "pipeline", "recipe"} only."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/_json.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["core", "execution", "pipeline", "recipe"]:
            assert pkg in dir_names, f"narrow cascade should include {pkg}"
        for excluded in ["config", "fleet", "migration", "workspace", "cli", "planner"]:
            assert excluded not in dir_names, f"narrow cascade should not include {excluded}"

    def test_type_constants_env_narrow_cascade(self, tmp_path: Path) -> None:
        """_type_constants_env → narrow cascade (smoke_utils silently dropped, no test dir)."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/types/_type_constants_env.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["core", "cli", "config", "execution", "recipe", "server", "workspace"]:
            assert pkg in dir_names, f"_type_constants_env cascade should include {pkg}"
        for excluded in ["fleet", "pipeline", "migration", "planner", "hooks"]:
            assert excluded not in dir_names, (
                f"_type_constants_env cascade should not include {excluded}"
            )

    def test_type_constants_features_narrow_cascade(self, tmp_path: Path) -> None:
        """_type_constants_features → narrow cascade of 7 dirs."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/types/_type_constants_features.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["core", "cli", "config", "fleet", "recipe", "server", "workspace"]:
            assert pkg in dir_names, f"_type_constants_features cascade should include {pkg}"
        for excluded in ["execution", "pipeline", "migration", "planner", "hooks"]:
            assert excluded not in dir_names, (
                f"_type_constants_features cascade should not include {excluded}"
            )

    def test_type_constants_registries_narrow_cascade(self, tmp_path: Path) -> None:
        """_type_constants_registries → narrow cascade of 7 dirs."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/types/_type_constants_registries.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["core", "cli", "config", "pipeline", "recipe", "server", "workspace"]:
            assert pkg in dir_names, f"_type_constants_registries cascade should include {pkg}"
        for excluded in ["execution", "fleet", "migration", "planner", "hooks"]:
            assert excluded not in dir_names, (
                f"_type_constants_registries cascade should not include {excluded}"
            )

    def test_type_exceptions_narrow_cascade(self, tmp_path: Path) -> None:
        """_type_exceptions → narrow cascade of 4 dirs."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/types/_type_exceptions.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["core", "fleet", "recipe", "server"]:
            assert pkg in dir_names, f"_type_exceptions cascade should include {pkg}"
        for excluded in [
            "cli",
            "config",
            "execution",
            "pipeline",
            "migration",
            "workspace",
            "hooks",
        ]:
            assert excluded not in dir_names, (
                f"_type_exceptions cascade should not include {excluded}"
            )

    def test_step_context_narrow_cascade(self, tmp_path: Path) -> None:
        """_step_context → narrow cascade of 4 dirs."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/_step_context.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["core", "execution", "pipeline", "server"]:
            assert pkg in dir_names, f"_step_context cascade should include {pkg}"
        for excluded in ["cli", "config", "fleet", "migration", "workspace", "recipe", "hooks"]:
            assert excluded not in dir_names, (
                f"_step_context cascade should not include {excluded}"
            )


class TestCoreStemCompleteness:
    """REQ-FILT-007: every core/ .py stem must be classified."""

    def test_all_core_stems_classified(self) -> None:
        core_root = Path("src/autoskillit/core")
        actual_stems = {p.stem for p in core_root.rglob("*.py") if p.stem != "__init__"}
        assert actual_stems, (
            f"No .py files found under {core_root} — is pytest running from the project root?"
        )
        classified = set(_CORE_UNIVERSAL_MODULES) | set(MODULE_CASCADE_CORE)
        unclassified = actual_stems - classified
        assert not unclassified, (
            f"Unclassified core stems (will fall through to full 18-dir cascade): "
            f"{sorted(unclassified)}"
        )


class TestClosureCoreNarrowCascade:
    """Closure-added core/__init__.py uses MODULE_CASCADE_CORE when all causes are narrow."""

    ALL_DIRS = [
        "core",
        "config",
        "execution",
        "pipeline",
        "workspace",
        "recipe",
        "migration",
        "fleet",
        "server",
        "cli",
        "hooks",
        "skills",
        "arch",
        "contracts",
        "infra",
        "docs",
    ]

    def _make_core_layout(self, tmp_path: Path, modules: dict[str, str]) -> Path:
        core_dir = tmp_path / "src" / "autoskillit" / "core"
        core_dir.mkdir(parents=True)
        for name, content in modules.items():
            (core_dir / name).write_text(content)
        tests_root = tmp_path / "tests"
        for d in self.ALL_DIRS:
            (tests_root / d).mkdir(parents=True, exist_ok=True)
        return tests_root

    def test_closure_init_uses_narrow_cascade_single_module(self, tmp_path: Path) -> None:
        """Single narrow cause → closure __init__.py uses that cause's MODULE_CASCADE_CORE."""
        tests_root = self._make_core_layout(
            tmp_path,
            {
                "_plugin_ids.py": "",
                "__init__.py": "from ._plugin_ids import DIRECT_PREFIX\n",
            },
        )
        result = build_test_scope(
            changed_files={"src/autoskillit/core/_plugin_ids.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["core", "cli", "server"]:
            assert pkg in dir_names, f"narrow cascade should include {pkg}"
        for excluded in [
            "execution",
            "pipeline",
            "workspace",
            "recipe",
            "migration",
            "hooks",
        ]:
            assert excluded not in dir_names, f"narrow cascade should not include {excluded}"

    def test_closure_init_uses_union_for_multiple_modules(self, tmp_path: Path) -> None:
        """Multiple narrow causes → union of their MODULE_CASCADE_CORE entries."""
        tests_root = self._make_core_layout(
            tmp_path,
            {
                "_plugin_ids.py": "",
                "_plugin_cache.py": "",
                "__init__.py": (
                    "from ._plugin_ids import DIRECT_PREFIX\n"
                    "from ._plugin_cache import is_cached\n"
                ),
            },
        )
        result = build_test_scope(
            changed_files={
                "src/autoskillit/core/_plugin_ids.py",
                "src/autoskillit/core/_plugin_cache.py",
            },
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        # Union: _plugin_ids={core,cli,server} ∪ _plugin_cache={core,cli,server}
        for pkg in ["core", "cli", "server"]:
            assert pkg in dir_names, f"union cascade should include {pkg}"
        for excluded in [
            "execution",
            "pipeline",
            "workspace",
            "recipe",
            "migration",
            "hooks",
        ]:
            assert excluded not in dir_names, f"union cascade should not include {excluded}"

    def test_closure_init_falls_back_when_universal_cause_present(self, tmp_path: Path) -> None:
        """A universal cause among the core changes → full cascade (fail-open)."""
        tests_root = self._make_core_layout(
            tmp_path,
            {
                "io.py": "",
                "kitchen_state.py": "",
                "__init__.py": (
                    "from .io import atomic_write\nfrom .kitchen_state import KitchenState\n"
                ),
            },
        )
        result = build_test_scope(
            changed_files={
                "src/autoskillit/core/io.py",
                "src/autoskillit/core/kitchen_state.py",
            },
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        # io is universal → full cascade
        for pkg in ["core", "execution", "pipeline", "server", "cli"]:
            assert pkg in dir_names, f"universal fallback should include {pkg}"

    def test_closure_init_falls_back_when_unknown_cause(self, tmp_path: Path) -> None:
        """An unmapped cause → full cascade (fail-open)."""
        tests_root = self._make_core_layout(
            tmp_path,
            {
                "kitchen_state.py": "",
                "_brand_new_module.py": "",
                "__init__.py": (
                    "from .kitchen_state import KitchenState\n"
                    "from ._brand_new_module import something\n"
                ),
            },
        )
        result = build_test_scope(
            changed_files={
                "src/autoskillit/core/kitchen_state.py",
                "src/autoskillit/core/_brand_new_module.py",
            },
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["core", "execution", "pipeline", "server", "cli"]:
            assert pkg in dir_names, f"unknown-cause fallback should include {pkg}"

    def test_closure_init_falls_back_when_no_core_causes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No core files in changed_src_py → full cascade (fail-open)."""
        tests_root = self._make_core_layout(
            tmp_path,
            {
                "__init__.py": "from .kitchen_state import KitchenState\n",
                "kitchen_state.py": "",
            },
        )

        original_expand = tf_mod._expand_reexport_closure

        def _patched_expand(changed_src_files: set[str], src_root: str | Path) -> set[str]:
            result = original_expand(changed_src_files, src_root)
            result.add("src/autoskillit/core/__init__.py")
            return result

        monkeypatch.setattr(tf_mod, "_expand_reexport_closure", _patched_expand)

        result = build_test_scope(
            changed_files={"src/autoskillit/server/__init__.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        # No core causes → full cascade for the closure-added __init__
        for pkg in ["core", "execution", "pipeline", "server", "cli"]:
            assert pkg in dir_names, f"no-causes fallback should include {pkg}"
