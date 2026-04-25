"""REQ-CORE-001..004: module-level cascade map for core/ submodules."""

from __future__ import annotations

from pathlib import Path

from tests._test_filter import (
    _CORE_UNIVERSAL_MODULES,
    MODULE_CASCADE_CORE,
    FilterMode,
    build_test_scope,
)


class TestCoreUniversalModules:
    """REQ-CORE-001: _CORE_UNIVERSAL_MODULES must exist and contain the right stems."""

    def test_required_stems_present(self) -> None:
        required = {
            "io",
            "logging",
            "types",
            "_type_constants",
            "_type_protocols",
            "_type_enums",
            "_type_subprocess",
            "_type_results",
            "_type_resume",
            "_type_helpers",
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

    def test_kitchen_state_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["kitchen_state"] == frozenset({"core", "cli"})

    def test_readiness_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["readiness"] == frozenset({"core", "server"})

    def test_feature_flags_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["feature_flags"] == frozenset(
            {"core", "cli", "config", "server", "workspace"}
        )

    def test_branch_guard_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["branch_guard"] == frozenset(
            {"core", "pipeline", "server", "workspace"}
        )

    def test_no_universal_stem_in_map(self) -> None:
        # Universal modules must not appear in MODULE_CASCADE_CORE
        overlap = _CORE_UNIVERSAL_MODULES & set(MODULE_CASCADE_CORE.keys())
        assert not overlap, f"Universal modules in MODULE_CASCADE_CORE: {overlap}"

    def test_all_13_entries_present(self) -> None:
        expected_stems = {
            "readiness",
            "feature_flags",
            "kitchen_state",
            "branch_guard",
            "_plugin_ids",
            "_terminal_table",
            "_linux_proc",
            "_plugin_cache",
            "github_url",
            "paths",
            "_claude_env",
            "_version_snapshot",
            "claude_conventions",
        }
        assert set(MODULE_CASCADE_CORE.keys()) == expected_stems


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
        "franchise",
        "server",
        "cli",
        "hooks",
        "skills",
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
        """kitchen_state.py → only {core, cli} + always-run."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/kitchen_state.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "core" in dir_names
        assert "cli" in dir_names
        # Must NOT include packages kitchen_state doesn't touch
        for excluded in [
            "execution",
            "pipeline",
            "workspace",
            "recipe",
            "migration",
            "server",
            "hooks",
        ]:
            assert excluded not in dir_names, (
                f"kitchen_state narrow cascade should not include {excluded}"
            )
        # arch and contracts always present; infra/docs not as dirs for non-triggering change
        assert "arch" in dir_names
        assert "contracts" in dir_names
        assert "infra" not in dir_names  # kitchen_state doesn't touch hooks/CI files
        assert "docs" not in dir_names  # kitchen_state doesn't touch docs files
        result_names = {p.name for p in result}
        from tests._test_filter import _INFRA_UNCONDITIONAL_FILES

        for fname in _INFRA_UNCONDITIONAL_FILES:
            assert fname in result_names, f"unconditional infra file {fname!r} missing"

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
            changed_files={"src/autoskillit/core/kitchen_state.py"},
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
            "franchise",
            "migration",
            "recipe",
            "server",
            "workspace",
        ]:
            assert pkg in dir_names

    def test_readiness_cascade_includes_only_server(self, tmp_path: Path) -> None:
        """readiness.py only used by server → {core, server} + always-run."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/readiness.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "core" in dir_names
        assert "server" in dir_names
        for excluded in [
            "execution",
            "pipeline",
            "workspace",
            "recipe",
            "migration",
            "cli",
            "hooks",
        ]:
            assert excluded not in dir_names
