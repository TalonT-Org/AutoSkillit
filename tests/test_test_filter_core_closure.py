"""Closure-aware core cascade selection."""

from __future__ import annotations

from pathlib import Path

import pytest

import tests._test_filter as tf_mod
from tests._test_filter import FilterMode, build_test_scope

pytestmark = [pytest.mark.medium]


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
        # workspace joined _plugin_ids' cascade when _install_state started reading
        # the plugin registry through registered_install_paths() — IL-005 forbids
        # workspace reaching for cli.InstalledPluginsFile.
        for pkg in ["core", "cli", "server", "workspace"]:
            assert pkg in dir_names, f"narrow cascade should include {pkg}"
        for excluded in [
            "execution",
            "pipeline",
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
        # Union: _plugin_ids={core,cli,server}
        # ∪ _plugin_cache={core,cli,server,workspace}
        for pkg in ["core", "cli", "server", "workspace"]:
            assert pkg in dir_names, f"union cascade should include {pkg}"
        for excluded in [
            "execution",
            "pipeline",
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
