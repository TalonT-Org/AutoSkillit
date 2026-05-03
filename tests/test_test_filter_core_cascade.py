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
            "_type_protocols_workspace",
            "_type_protocols_recipe",
            "_type_protocols_infra",
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

    def test_feature_flags_cascade(self) -> None:
        assert MODULE_CASCADE_CORE["feature_flags"] == frozenset(
            {"core", "cli", "config", "recipe", "server", "workspace"}
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
            "feature_flags",
            "branch_guard",
            "_plugin_ids",
            "_terminal_table",
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

    def test_kitchen_state_fails_open_to_full_cascade(self, tmp_path: Path) -> None:
        """kitchen_state.py (now in runtime/) → fail-open to full core cascade."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/runtime/kitchen_state.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        # Fail-open: kitchen_state stem not in MODULE_CASCADE_CORE → full core cascade
        for pkg in ["core", "config", "execution", "server", "cli"]:
            assert pkg in dir_names, f"fail-open should include {pkg}"

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

    def test_readiness_cascade_includes_only_server(self, tmp_path: Path) -> None:
        """readiness.py only used by server → {core, server} + always-run."""
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/core/runtime/readiness.py"},
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
