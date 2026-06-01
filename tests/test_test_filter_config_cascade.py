"""REQ-CONFIG-001..003: module-level cascade map for config/ submodules."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_filter import (
    MODULE_CASCADE_CONFIG,
    FilterMode,
    build_test_scope,
)

pytestmark = [pytest.mark.medium]

_ALL_DIRS = [
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


def test_all_entries_present() -> None:
    expected = {"_config_loader", "ingredient_defaults"}
    assert expected <= set(MODULE_CASCADE_CONFIG.keys())


class TestModuleCascadeConfig:
    def test_all_values_are_frozensets(self) -> None:
        for stem, consumers in MODULE_CASCADE_CONFIG.items():
            assert isinstance(consumers, frozenset), f"{stem} value must be frozenset"

    def test_all_consumers_include_config(self) -> None:
        for stem, consumers in MODULE_CASCADE_CONFIG.items():
            assert "config" in consumers, f"{stem} cascade must include 'config'"


class TestBuildTestScopeConfigCascade:
    ALL_DIRS = _ALL_DIRS

    def _make_tests_root(self, tmp_path: Path, dirs: list[str]) -> Path:
        tests_root = tmp_path / "tests"
        for d in dirs:
            (tests_root / d).mkdir(parents=True, exist_ok=True)
        return tests_root

    def test_narrow_config_module_uses_narrow_scope(self, tmp_path: Path) -> None:
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/config/_config_loader.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "config" in dir_names
        for excluded in ["execution", "fleet", "pipeline", "workspace"]:
            assert excluded not in dir_names, (
                f"narrow cascade for _config_loader should not include {excluded}"
            )
        assert "arch" in dir_names
        assert "contracts" in dir_names

    def test_ingredient_defaults_uses_narrow_scope(self, tmp_path: Path) -> None:
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/config/ingredient_defaults.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "config" in dir_names
        for excluded in ["execution", "fleet", "pipeline", "workspace"]:
            assert excluded not in dir_names, (
                f"narrow cascade for ingredient_defaults should not include {excluded}"
            )
        assert "arch" in dir_names
        assert "contracts" in dir_names

    def test_settings_falls_through_to_full_cascade(self, tmp_path: Path) -> None:
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/config/settings.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["config", "execution", "server", "cli"]:
            assert pkg in dir_names, f"fail-open cascade for settings.py should include {pkg}"

    def test_config_dataclasses_falls_through_to_full_cascade(self, tmp_path: Path) -> None:
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/config/_config_dataclasses.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["config", "execution", "server", "cli"]:
            assert pkg in dir_names

    def test_config_init_triggers_full_cascade(self, tmp_path: Path) -> None:
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/config/__init__.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        for pkg in ["config", "execution", "server", "cli"]:
            assert pkg in dir_names

    def test_aggressive_mode_skips_config_cascade_branch(self, tmp_path: Path) -> None:
        tests_root = self._make_tests_root(tmp_path, self.ALL_DIRS)
        result = build_test_scope(
            changed_files={"src/autoskillit/config/_config_loader.py"},
            mode=FilterMode.AGGRESSIVE,
            tests_root=tests_root,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "config" in dir_names
        for excluded in ["execution", "fleet", "pipeline", "workspace", "server", "cli"]:
            assert excluded not in dir_names


class TestClosureConfigNarrowCascade:
    ALL_DIRS = _ALL_DIRS

    def _make_config_layout(self, tmp_path: Path, modules: dict[str, str]) -> Path:
        config_dir = tmp_path / "src" / "autoskillit" / "config"
        config_dir.mkdir(parents=True)
        for name, content in modules.items():
            (config_dir / name).write_text(content)
        tests_root = tmp_path / "tests"
        for d in self.ALL_DIRS:
            (tests_root / d).mkdir(parents=True, exist_ok=True)
        return tests_root

    def test_init_closure_narrow_single_cause(self, tmp_path: Path) -> None:
        tests_root = self._make_config_layout(
            tmp_path,
            {
                "__init__.py": "from ._config_loader import load_config\n",
                "_config_loader.py": "def load_config(): ...\n",
            },
        )
        result = build_test_scope(
            changed_files={"src/autoskillit/config/_config_loader.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
            cwd=tmp_path,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "config" in dir_names
        for excluded in ["execution", "fleet", "pipeline", "workspace"]:
            assert excluded not in dir_names

    def test_init_closure_mixed_causes_fail_open(self, tmp_path: Path) -> None:
        tests_root = self._make_config_layout(
            tmp_path,
            {
                "__init__.py": (
                    "from ._config_loader import load_config\n"
                    "from .settings import AutomationConfig\n"
                ),
                "_config_loader.py": "def load_config(): ...\n",
                "settings.py": "class AutomationConfig: ...\n",
            },
        )
        result = build_test_scope(
            changed_files={
                "src/autoskillit/config/_config_loader.py",
                "src/autoskillit/config/settings.py",
            },
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
            cwd=tmp_path,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "config" in dir_names
        for pkg in ["execution", "fleet", "pipeline", "workspace", "server", "cli"]:
            assert pkg in dir_names, f"fail-open cascade should include {pkg}"

    def test_init_closure_only_init_fails_open(self, tmp_path: Path) -> None:
        tests_root = self._make_config_layout(
            tmp_path,
            {
                "__init__.py": "from .settings import AutomationConfig\n",
                "settings.py": "class AutomationConfig: ...\n",
            },
        )
        result = build_test_scope(
            changed_files={"src/autoskillit/config/__init__.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=tests_root,
            cwd=tmp_path,
        )
        assert result is not None
        dir_names = {p.name for p in result}
        assert "config" in dir_names
