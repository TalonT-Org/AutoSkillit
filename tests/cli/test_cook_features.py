"""Tests: cook CLI features — subset gate, recipes CLI, fleet display categories."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit import cli
from tests.cli.conftest import _GITHUB_RECIPE_YAML

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


class TestOrderSubsetGate:
    """Tests for the order-time subset-disabled gate (T-VAL-008..010)."""

    @pytest.fixture(autouse=True)
    def _stub_preview(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("autoskillit.cli._prompts.show_cook_preview", lambda *a, **kw: None)

    @pytest.fixture(autouse=True)
    def _stub_ingredients_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import importlib
        import sys as _sys

        _app_mod = _sys.modules.get("autoskillit.cli.app") or importlib.import_module(
            "autoskillit.cli.app"
        )
        monkeypatch.setattr(_app_mod, "_get_ingredients_table", lambda *a, **kw: "| col | val |")

    def _make_config_mock(self, disabled: list[str]) -> MagicMock:
        mock_cfg = MagicMock()
        mock_cfg.subsets.disabled = disabled
        return mock_cfg

    def test_order_hard_error_non_interactive_on_disabled_subset(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """T-VAL-008: order exits 1 non-interactively when recipe references a disabled subset."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "github-recipe.yaml").write_text(_GITHUB_RECIPE_YAML)

        monkeypatch.setattr(
            "autoskillit.config.load_config", lambda *a, **kw: self._make_config_mock(["github"])
        )
        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = False
        monkeypatch.setattr("sys.stdin", mock_stdin)

        with pytest.raises(SystemExit) as exc_info:
            cli.order("github-recipe")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "requires subset" in captured.out.lower()

    @patch("autoskillit.cli.subprocess.run")
    def test_order_enable_temporarily_sets_env_override(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-VAL-009: order injects AUTOSKILLIT_SUBSETS__DISABLED env override for temp enable."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "github-recipe.yaml").write_text(_GITHUB_RECIPE_YAML)

        monkeypatch.setattr(
            "autoskillit.config.load_config", lambda *a, **kw: self._make_config_mock(["github"])
        )
        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = True
        monkeypatch.setattr("sys.stdin", mock_stdin)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        inputs = iter(["1", ""])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.order("github-recipe")

        mock_run.assert_called_once()
        passed_env = mock_run.call_args.kwargs["env"]
        assert "AUTOSKILLIT_SUBSETS__DISABLED" in passed_env
        assert passed_env["AUTOSKILLIT_SUBSETS__DISABLED"] == "@json []"

    def test_order_enable_permanently_updates_config(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-VAL-010: order calls _enable_subsets_permanently on enable-permanently choice."""
        import importlib
        import sys as _sys

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "github-recipe.yaml").write_text(_GITHUB_RECIPE_YAML)

        monkeypatch.setattr(
            "autoskillit.config.load_config", lambda *a, **kw: self._make_config_mock(["github"])
        )
        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = True
        monkeypatch.setattr("sys.stdin", mock_stdin)

        called_with: list = []

        def _fake_enable(project_dir, subsets):
            called_with.append((project_dir, subsets))

        _app_mod = _sys.modules.get("autoskillit.cli.app") or importlib.import_module(
            "autoskillit.cli.app"
        )
        monkeypatch.setattr(_app_mod, "_enable_subsets_permanently", _fake_enable)
        inputs = iter(["2", "n"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
        monkeypatch.setattr("autoskillit.cli._ansi.permissions_warning", lambda: "")

        cli.order("github-recipe")

        assert called_with, "_enable_subsets_permanently was not called"
        _, subsets = called_with[0]
        assert "github" in subsets


class TestRecipesCLI:
    def test_recipes_list_outputs_names(self, capsys: pytest.CaptureFixture) -> None:
        """recipes list prints at least one recipe name to stdout."""
        import importlib
        import sys

        _app_mod = sys.modules.get("autoskillit.cli.app") or importlib.import_module(
            "autoskillit.cli.app"
        )
        _app_mod.recipes_list()
        captured = capsys.readouterr()
        assert captured.out.strip(), "Expected at least one recipe in output"
        assert "implementation" in captured.out

    def test_recipes_show_prints_content(self, capsys: pytest.CaptureFixture) -> None:
        """recipes show prints the YAML content of a known bundled recipe."""
        import importlib
        import sys

        _app_mod = sys.modules.get("autoskillit.cli.app") or importlib.import_module(
            "autoskillit.cli.app"
        )
        _app_mod.recipes_show("smoke-test")
        captured = capsys.readouterr()
        assert captured.out.strip(), "Expected YAML content in output"
        assert "smoke-test" in captured.out

    def test_recipes_show_unknown_exits(self, capsys: pytest.CaptureFixture) -> None:
        """recipes show exits 1 for an unknown recipe name."""
        import importlib
        import sys

        _app_mod = sys.modules.get("autoskillit.cli.app") or importlib.import_module(
            "autoskillit.cli.app"
        )
        with pytest.raises(SystemExit) as exc_info:
            _app_mod.recipes_show("nonexistent-recipe-xyz")
        assert exc_info.value.code == 1

    def test_recipes_render_lists_available(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """DG-22: `autoskillit recipes render` subcommand is registered and lists recipes."""
        from autoskillit import cli

        monkeypatch.chdir(tmp_path)
        cli.recipes_render(None)
        captured = capsys.readouterr()
        assert captured.out.strip(), "Expected recipe names in output"
        assert "implementation" in captured.out


def test_display_categories_omits_fleet_when_disabled() -> None:
    """Fleet category must not appear in iter_display_categories output when fleet is disabled."""
    from autoskillit.config import iter_display_categories

    cfg_features: dict[str, bool] = {"fleet": False}
    categories = [name for name, _ in iter_display_categories(cfg_features)]
    assert "Fleet" not in categories


def test_display_categories_includes_fleet_when_enabled() -> None:
    """Fleet category must appear in iter_display_categories output when fleet is enabled."""
    from autoskillit.config import iter_display_categories

    cfg_features: dict[str, bool] = {"fleet": True}
    categories = [name for name, _ in iter_display_categories(cfg_features)]
    assert "Fleet" in categories
