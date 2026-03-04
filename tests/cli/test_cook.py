"""Tests for CLI cook, workspace, and skills list commands."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit import cli

_SCRIPT_YAML = """\
name: test-script
description: A test script
summary: Test flow
ingredients:
  target:
    description: Target path
    required: true
steps:
  do-something:
    tool: run_cmd
    with:
      cmd: echo hello
    on_success: done
    on_failure: done
  done:
    action: stop
    message: Finished
kitchen_rules:
  - Only use AutoSkillit MCP tools during pipeline execution
"""


class TestCLICook:
    # --- workspace init ---

    def test_prep_station_init_creates_dir_with_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """prep station init creates directory and drops marker file."""
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "test-workspace"
        cli.workspace_init(str(target))
        assert target.is_dir()
        assert (target / ".autoskillit-workspace").is_file()

    def test_prep_station_init_refuses_nonempty_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """prep station init refuses to initialize a non-empty directory."""
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "existing"
        target.mkdir()
        (target / "important.txt").touch()
        with pytest.raises(SystemExit):
            cli.workspace_init(str(target))

    def test_prep_station_init_idempotent_on_empty_with_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """prep station init is safe to re-run on a directory that only has the marker."""
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "prep-station"
        cli.workspace_init(str(target))
        cli.workspace_init(str(target))  # second run — should not fail
        assert (target / ".autoskillit-workspace").is_file()

    def test_prep_station_init_marker_has_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Marker file contains human-readable identifying content."""
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "prep-station"
        cli.workspace_init(str(target))
        content = (target / ".autoskillit-workspace").read_text()
        assert "autoskillit" in content
        assert "do not delete" in content

    # --- T_WC: workspace clean ---

    def test_workspace_clean_removes_subdirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """T_WC1: workspace_clean removes all subdirs of autoskillit-runs/."""
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "run-a").mkdir(parents=True)
        (runs_dir / "run-b").mkdir(parents=True)
        cli.workspace_clean(dir=str(tmp_path))
        assert not (runs_dir / "run-a").exists()
        assert not (runs_dir / "run-b").exists()

    def test_workspace_clean_reports_nothing_when_no_runs_dir(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """T_WC2: workspace_clean prints message when autoskillit-runs/ doesn't exist."""
        cli.workspace_clean(dir=str(tmp_path))
        captured = capsys.readouterr()
        assert "No autoskillit-runs/" in captured.out

    def test_workspace_clean_defaults_to_parent_of_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T_WC3: workspace_clean without --dir uses parent of CWD."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        monkeypatch.chdir(project_dir)
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "run-x").mkdir(parents=True)
        cli.workspace_clean()
        assert not (runs_dir / "run-x").exists()

    # --- T6: skills list CLI output ---

    def test_skills_list_shows_all_sources(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """skills list outputs skill names with source labels."""
        monkeypatch.chdir(tmp_path)
        cli.skills_list()
        captured = capsys.readouterr()
        assert "investigate" in captured.out
        assert "bundled" in captured.out
        assert "NAME" in captured.out

    # --- cook ---

    def test_cook_blocked_inside_claude_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """cook exits 1 when CLAUDECODE env var is set."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CLAUDECODE", "1")
        with pytest.raises(SystemExit) as exc_info:
            cli.cook("any-script")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "regular terminal" in captured.out.lower()

    def test_cook_script_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """cook exits 1 when script name doesn't match any entry."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)

        with pytest.raises(SystemExit) as exc_info:
            cli.cook("nonexistent")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "nonexistent" in captured.out

    def test_cook_no_scripts_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """cook exits 1 with available bundled recipes listed when name not found."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            cli.cook("anything")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Recipe not found: 'anything'" in captured.out
        assert "Available recipes:" in captured.out
        assert "implementation" in captured.out

    def test_cook_available_scripts_listed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """cook lists available scripts when name doesn't match."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)

        with pytest.raises(SystemExit) as exc_info:
            cli.cook("nonexistent")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Available recipes:" in captured.out
        assert "test-script" in captured.out

    def test_cook_claude_not_on_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """cook exits 1 when claude command is not found."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: None)

        with pytest.raises(SystemExit) as exc_info:
            cli.cook("test-script")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "claude" in captured.out.lower()

    def test_cook_invalid_script_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """cook exits 1 when script YAML fails validation."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        # Script with no steps (empty mapping) — will fail validation
        (scripts_dir / "bad-script.yaml").write_text("name: bad-script\nsteps: {}\n")

        with pytest.raises(SystemExit) as exc_info:
            cli.cook("bad-script")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "validation" in captured.out.lower() or "error" in captured.out.lower()

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_builds_correct_command(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cook passes correct flags to subprocess.run."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.cook("test-script")

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"
        assert "--plugin-dir" in cmd
        plugin_dir_idx = cmd.index("--plugin-dir")
        plugin_dir_val = Path(cmd[plugin_dir_idx + 1])
        assert plugin_dir_val.is_dir()
        assert (plugin_dir_val / ".claude-plugin" / "plugin.json").is_file()
        assert "--tools" in cmd
        tools_idx = cmd.index("--tools")
        assert cmd[tools_idx + 1] == "AskUserQuestion"
        assert "--append-system-prompt" in cmd
        # Interactive: must have --allow-dangerous-permissions, no -p, no
        # --dangerously-skip-permissions
        assert "--allow-dangerous-permissions" in cmd
        assert "-p" not in cmd
        assert "--dangerously-skip-permissions" not in cmd
        # Interactive passthrough: no capture_output, no stdin
        kwargs = mock_run.call_args[1] if mock_run.call_args[1] else {}
        assert "capture_output" not in kwargs
        assert "stdin" not in kwargs

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_system_prompt_contains_script(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cook injects script YAML and orchestrator contract into system prompt."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.cook("test-script")

        cmd = mock_run.call_args[0][0]
        prompt_idx = cmd.index("--append-system-prompt")
        system_prompt = cmd[prompt_idx + 1]
        # Contains the script YAML content
        assert "test-script" in system_prompt
        assert "do-something" in system_prompt
        # Contains routing rules
        assert "ROUTING RULES" in system_prompt
        # Contains failure predicates
        assert "FAILURE PREDICATES" in system_prompt
        # Contains tool discipline block
        assert "capture:" in system_prompt
        assert "${{ context." in system_prompt
        assert "AutoSkillit MCP tools" in system_prompt

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_propagates_exit_code(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cook does not raise SystemExit on returncode 0."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.cook("test-script")  # should not raise
        mock_run.assert_called_once()

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_subprocess_failure_propagates(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cook propagates non-zero subprocess exit codes."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=42, stdout="", stderr=""
        )

        with pytest.raises(SystemExit) as exc_info:
            cli.cook("test-script")
        assert exc_info.value.code == 42

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_no_recipe_prompts_user(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cook prompts for recipe name when none is provided."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        monkeypatch.setattr("builtins.input", lambda _prompt="": "test-script")

        cli.cook()  # no recipe argument

        mock_run.assert_called_once()

    def test_cook_no_recipe_no_available_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """cook exits 1 when no recipe is given and no recipes are available."""
        import importlib
        import sys
        from unittest.mock import MagicMock as _MagicMock

        # autoskillit.cli.__init__ exports 'app' (cyclopts App), shadowing the submodule
        # attribute. Use sys.modules to get the actual cli.app module for patching.
        _app_mod = sys.modules.get("autoskillit.cli.app") or importlib.import_module(
            "autoskillit.cli.app"
        )
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        mock_result = _MagicMock()
        mock_result.items = []
        monkeypatch.setattr(_app_mod, "list_recipes", lambda _: mock_result)

        with pytest.raises(SystemExit) as exc_info:
            cli.cook()
        assert exc_info.value.code == 1

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_named_recipe_skips_prompt(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cook does not prompt for recipe when name is provided directly."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        input_called = []
        monkeypatch.setattr("builtins.input", lambda *a, **kw: input_called.append(1) or "")

        cli.cook("test-script")

        assert not input_called, "input() must not be called when recipe name is provided"

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_uses_allow_dangerous_permissions(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cook passes --allow-dangerous-permissions to claude."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.cook("test-script")

        cmd = mock_run.call_args[0][0]
        assert "--allow-dangerous-permissions" in cmd

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_env_has_kitchen_open(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cook passes AUTOSKILLIT_KITCHEN_OPEN=1 in the subprocess environment."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.cook("test-script")

        kwargs = mock_run.call_args[1] if mock_run.call_args[1] else {}
        assert "env" in kwargs
        assert kwargs["env"].get("AUTOSKILLIT_KITCHEN_OPEN") == "1"

    def test_cook_recipe_not_found_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """cook exits 1 when the given recipe name is not found."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            cli.cook("totally-unknown-recipe-xyz")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "totally-unknown-recipe-xyz" in captured.out

    def test_cook_malformed_yaml_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """cook exits 1 with YAML parse error message when load_recipe raises YAMLError."""
        from autoskillit.core import YAMLError

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        with (
            patch("autoskillit.recipe.find_recipe_by_name", return_value=MagicMock()),
            patch("autoskillit.recipe.load_recipe", side_effect=YAMLError("bad yaml")),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli.cook("bad-recipe")

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "YAML parse error" in captured.out

    def test_cook_structure_error_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """cook exits 1 with structure error message when load_recipe raises ValueError."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        with (
            patch("autoskillit.recipe.find_recipe_by_name", return_value=MagicMock()),
            patch("autoskillit.recipe.load_recipe", side_effect=ValueError("bad structure")),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli.cook("bad-recipe")

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "structure error" in captured.out

    def test_cook_picker_empty_input_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """cook exits 1 when picker receives empty input (empty name → not found)."""
        import importlib
        import sys

        _app_mod = sys.modules.get("autoskillit.cli.app") or importlib.import_module(
            "autoskillit.cli.app"
        )
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        fake_recipe = MagicMock()
        fake_recipe.name = "some-recipe"
        mock_result = MagicMock()
        mock_result.items = [fake_recipe]
        monkeypatch.setattr(_app_mod, "list_recipes", lambda _: mock_result)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")

        with pytest.raises(SystemExit) as exc_info:
            cli.cook()

        assert exc_info.value.code == 1


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

    # DG-22
    def test_recipes_render_renders_bundled(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """DG-22: `autoskillit recipes render` subcommand is registered and callable."""
        from autoskillit import cli

        monkeypatch.chdir(tmp_path)
        cli.recipes_render(None)  # render all bundled
        captured = capsys.readouterr()
        assert "Rendered:" in captured.out
