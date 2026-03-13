"""Tests for CLI cook, workspace, and skills list commands."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit import cli
from autoskillit.cli._prompts import _OPEN_KITCHEN_CHOICE, _resolve_recipe_input
from autoskillit.cli._workspace import _format_age
from autoskillit.core import ClaudeFlags

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


class TestResolveRecipeInput:
    """Unit tests for the _resolve_recipe_input picker resolution helper."""

    def _make_recipe(self, name: str) -> MagicMock:
        r = MagicMock()
        r.name = name
        return r

    def test_zero_returns_open_kitchen_sentinel(self) -> None:
        available = [self._make_recipe("smoke-test")]
        result = _resolve_recipe_input("0", available)
        assert result is _OPEN_KITCHEN_CHOICE

    def test_zero_with_empty_list_returns_open_kitchen_sentinel(self) -> None:
        result = _resolve_recipe_input("0", [])
        assert result is _OPEN_KITCHEN_CHOICE

    def test_valid_number_first_returns_first_recipe(self) -> None:
        r1 = self._make_recipe("implementation")
        r2 = self._make_recipe("bugfix-loop")
        assert _resolve_recipe_input("1", [r1, r2]) is r1

    def test_valid_number_last_returns_last_recipe(self) -> None:
        r1 = self._make_recipe("implementation")
        r2 = self._make_recipe("bugfix-loop")
        assert _resolve_recipe_input("2", [r1, r2]) is r2

    def test_out_of_range_too_high_returns_none(self) -> None:
        available = [self._make_recipe("smoke-test")]
        assert _resolve_recipe_input("99", available) is None

    def test_out_of_range_negative_digit_treated_as_name(self) -> None:
        # "-1".isdigit() is False in Python — treated as name lookup, returns None
        available = [self._make_recipe("smoke-test")]
        assert _resolve_recipe_input("-1", available) is None

    def test_name_match_returns_recipe(self) -> None:
        r = self._make_recipe("smoke-test")
        assert _resolve_recipe_input("smoke-test", [r, self._make_recipe("other")]) is r

    def test_name_no_match_returns_none(self) -> None:
        available = [self._make_recipe("smoke-test")]
        assert _resolve_recipe_input("nonexistent", available) is None

    def test_empty_string_returns_none(self) -> None:
        available = [self._make_recipe("smoke-test")]
        assert _resolve_recipe_input("", available) is None


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
        """T_WC1: workspace_clean removes stale subdirs of autoskillit-runs/."""
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "run-a").mkdir(parents=True)
        (runs_dir / "run-b").mkdir(parents=True)
        old_time = time.time() - 6 * 3600
        for d in (runs_dir / "run-a", runs_dir / "run-b"):
            os.utime(d, (old_time, old_time))
        cli.workspace_clean(dir=str(tmp_path), force=True)
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
        old_time = time.time() - 6 * 3600
        os.utime(runs_dir / "run-x", (old_time, old_time))
        cli.workspace_clean(force=True)
        assert not (runs_dir / "run-x").exists()

    def test_workspace_clean_recent_dirs_skipped(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """T_WC4: recent dirs (<5h) are not deleted, stale dirs (>5h) are."""
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "recent").mkdir(parents=True)
        (runs_dir / "stale").mkdir(parents=True)
        old_time = time.time() - 6 * 3600
        os.utime(runs_dir / "stale", (old_time, old_time))
        cli.workspace_clean(dir=str(tmp_path), force=True)
        assert (runs_dir / "recent").exists()
        assert not (runs_dir / "stale").exists()

    def test_workspace_clean_boundary_5h_is_stale(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """T_WC5: dir with mtime exactly 5h ago is stale (>=5h threshold)."""
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "boundary").mkdir(parents=True)
        boundary_time = time.time() - 5 * 3600
        os.utime(runs_dir / "boundary", (boundary_time, boundary_time))
        cli.workspace_clean(dir=str(tmp_path), force=True)
        assert not (runs_dir / "boundary").exists()

    def test_workspace_clean_skipped_dirs_printed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """T_WC6: skipped (recent) dirs are printed with age."""
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "recent-one").mkdir(parents=True)
        recent_time = time.time() - 3600
        os.utime(runs_dir / "recent-one", (recent_time, recent_time))
        cli.workspace_clean(dir=str(tmp_path), force=True)
        captured = capsys.readouterr()
        assert "Skipped" in captured.out
        assert "recent-one" in captured.out

    def test_workspace_clean_will_remove_printed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """T_WC7: stale dirs are printed as 'Will remove' with age."""
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "old-one").mkdir(parents=True)
        old_time = time.time() - 10 * 3600
        os.utime(runs_dir / "old-one", (old_time, old_time))
        cli.workspace_clean(dir=str(tmp_path), force=True)
        captured = capsys.readouterr()
        assert "Will remove" in captured.out
        assert "old-one" in captured.out

    def test_workspace_clean_confirm_defaults_no(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T_WC8: empty input at confirmation prompt defaults to N (no deletion)."""
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "stale").mkdir(parents=True)
        old_time = time.time() - 6 * 3600
        os.utime(runs_dir / "stale", (old_time, old_time))
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        cli.workspace_clean(dir=str(tmp_path))
        assert (runs_dir / "stale").exists()

    def test_workspace_clean_confirm_accepts_y(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T_WC9: 'y' at confirmation prompt deletes stale dirs."""
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "stale").mkdir(parents=True)
        old_time = time.time() - 6 * 3600
        os.utime(runs_dir / "stale", (old_time, old_time))
        monkeypatch.setattr("builtins.input", lambda _prompt="": "y")
        cli.workspace_clean(dir=str(tmp_path))
        assert not (runs_dir / "stale").exists()

    def test_workspace_clean_force_skips_prompt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T_WC10: --force deletes without calling input()."""
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "stale").mkdir(parents=True)
        old_time = time.time() - 6 * 3600
        os.utime(runs_dir / "stale", (old_time, old_time))
        monkeypatch.setattr(
            "builtins.input",
            lambda _prompt="": (_ for _ in ()).throw(AssertionError("input() called")),
        )
        cli.workspace_clean(dir=str(tmp_path), force=True)
        assert not (runs_dir / "stale").exists()

    def test_workspace_clean_no_stale_nothing_to_clean(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """T_WC11: only recent dirs prints nothing-to-clean message."""
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "recent").mkdir(parents=True)
        cli.workspace_clean(dir=str(tmp_path), force=True)
        captured = capsys.readouterr()
        assert "Nothing to clean" in captured.out
        assert (runs_dir / "recent").exists()

    def test_format_age_various_values(self) -> None:
        """T_WC14: _format_age returns human-readable age strings."""
        assert _format_age(30 * 60) == "30m ago"
        assert _format_age(2 * 3600 + 14 * 60) == "2h 14m ago"
        assert _format_age(3 * 86400) == "3d ago"
        assert _format_age(5 * 3600) == "5h ago"

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
        assert ClaudeFlags.PLUGIN_DIR in cmd
        plugin_dir_idx = cmd.index(ClaudeFlags.PLUGIN_DIR)
        plugin_dir_val = Path(cmd[plugin_dir_idx + 1])
        assert plugin_dir_val.is_dir()
        assert (plugin_dir_val / ".claude-plugin" / "plugin.json").is_file()
        assert ClaudeFlags.TOOLS in cmd
        tools_idx = cmd.index(ClaudeFlags.TOOLS)
        assert cmd[tools_idx + 1] == "AskUserQuestion"
        assert ClaudeFlags.APPEND_SYSTEM_PROMPT in cmd
        # Interactive cook: must have --dangerously-skip-permissions, no -p
        assert ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS in cmd
        assert ClaudeFlags.PRINT not in cmd
        assert ClaudeFlags.ALLOW_DANGEROUSLY_SKIP_PERMISSIONS not in cmd
        # Interactive passthrough: no capture_output, no stdin
        kwargs = mock_run.call_args[1] if mock_run.call_args[1] else {}
        assert "capture_output" not in kwargs
        assert "stdin" not in kwargs

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_system_prompt_contains_behavioral_instructions(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cook injects recipe name and behavioral instructions into system prompt."""
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
        prompt_idx = cmd.index(ClaudeFlags.APPEND_SYSTEM_PROMPT)
        system_prompt = cmd[prompt_idx + 1]
        # Contains recipe name
        assert "test-script" in system_prompt
        # Instructs Claude to call load_recipe
        assert "load_recipe" in system_prompt
        # Contains routing rules
        assert "ROUTING RULES" in system_prompt
        # Contains failure predicates
        assert "FAILURE PREDICATES" in system_prompt
        # Contains tool discipline block
        assert "capture:" in system_prompt
        assert "${{ context." in system_prompt
        assert "AutoSkillit MCP tools" in system_prompt
        # Does NOT contain raw recipe YAML body
        assert "--- RECIPE ---" not in system_prompt
        assert "do-something" not in system_prompt

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
        from unittest.mock import MagicMock as _MagicMock

        import autoskillit.recipe as _recipe_mod

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        mock_result = _MagicMock()
        mock_result.items = []
        monkeypatch.setattr(_recipe_mod, "list_recipes", lambda _: mock_result)

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
    def test_cook_uses_dangerously_skip_permissions(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cook passes --dangerously-skip-permissions to claude."""
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
        assert ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS in cmd

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

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_picker_shows_zero_option(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Picker output includes '0. Open kitchen' line."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "test-script")

        cli.cook()

        captured = capsys.readouterr()
        assert "0. Open kitchen" in captured.out

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_picker_prompt_includes_range(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Picker prompt text includes 'Select recipe [0-'."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)

        prompts_seen: list[str] = []
        monkeypatch.setattr(
            "builtins.input", lambda prompt="": prompts_seen.append(prompt) or "test-script"
        )

        cli.cook()

        assert any("Select recipe [0-" in p for p in prompts_seen)

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_picker_accepts_number_launches_recipe(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Typing '1' in the picker selects the first recipe and launches it."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "1")

        cli.cook()

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert ClaudeFlags.APPEND_SYSTEM_PROMPT in cmd

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_picker_zero_launches_open_kitchen(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Typing '0' launches a session without a recipe YAML in the system prompt."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "0")

        cli.cook()

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        system_prompt_idx = cmd.index(ClaudeFlags.APPEND_SYSTEM_PROMPT) + 1
        assert "--- RECIPE ---" not in cmd[system_prompt_idx]

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_picker_zero_system_prompt_contains_kitchen_open(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Option 0 injects an open-kitchen system prompt, not a recipe orchestrator."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "0")

        cli.cook()

        cmd = mock_run.call_args[0][0]
        system_prompt_idx = cmd.index(ClaudeFlags.APPEND_SYSTEM_PROMPT) + 1
        assert "open_kitchen" in cmd[system_prompt_idx]

    def test_cook_picker_out_of_range_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Out-of-range numeric input exits 1 with an error message."""
        import autoskillit.recipe as _recipe_mod

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        fake_recipe = MagicMock()
        fake_recipe.name = "some-recipe"
        mock_result = MagicMock()
        mock_result.items = [fake_recipe]
        monkeypatch.setattr(_recipe_mod, "list_recipes", lambda _: mock_result)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "99")

        with pytest.raises(SystemExit) as exc_info:
            cli.cook()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Invalid selection" in captured.out

    def test_cook_picker_invalid_name_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """Unknown recipe name exits 1 with an error message."""
        import autoskillit.recipe as _recipe_mod

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        fake_recipe = MagicMock()
        fake_recipe.name = "some-recipe"
        mock_result = MagicMock()
        mock_result.items = [fake_recipe]
        monkeypatch.setattr(_recipe_mod, "list_recipes", lambda _: mock_result)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "no-such-recipe")

        with pytest.raises(SystemExit) as exc_info:
            cli.cook()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Invalid selection" in captured.out

    def test_cook_picker_empty_input_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """cook exits 1 when picker receives empty input (empty name → not found)."""
        import autoskillit.recipe as _recipe_mod

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        fake_recipe = MagicMock()
        fake_recipe.name = "some-recipe"
        mock_result = MagicMock()
        mock_result.items = [fake_recipe]
        monkeypatch.setattr(_recipe_mod, "list_recipes", lambda _: mock_result)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")

        with pytest.raises(SystemExit) as exc_info:
            cli.cook()

        assert exc_info.value.code == 1


class TestCookDisplayOwnership:
    """cook() delegates recipe display to the Claude session via load_recipe."""

    def _setup_recipe(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Write test recipe to scripts_dir and chdir."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        return scripts_dir

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_system_prompt_does_not_contain_recipe_yaml(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """System prompt must contain recipe NAME but not the raw YAML body."""
        self._setup_recipe(tmp_path, monkeypatch)
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.cook("test-script")

        cmd = mock_run.call_args[0][0]
        prompt_idx = cmd.index(ClaudeFlags.APPEND_SYSTEM_PROMPT)
        system_prompt = cmd[prompt_idx + 1]
        assert "test-script" in system_prompt
        assert "--- RECIPE ---" not in system_prompt
        assert "--- END RECIPE ---" not in system_prompt
        assert "steps:" not in system_prompt
        assert "on_success:" not in system_prompt

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_does_not_print_recipe_before_launch(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """cook() must not dump recipe info to terminal. Display is Claude's job."""
        self._setup_recipe(tmp_path, monkeypatch)
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.cook("test-script")

        captured = capsys.readouterr()
        assert "TEST-SCRIPT" not in captured.out, (
            "Recipe name must not be pre-rendered to terminal"
        )
        assert "Kitchen Rules" not in captured.out, "Kitchen rules must not appear before launch"

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_system_prompt_instructs_load_recipe(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """System prompt must instruct Claude to call load_recipe as its first action."""
        self._setup_recipe(tmp_path, monkeypatch)
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.cook("test-script")

        cmd = mock_run.call_args[0][0]
        prompt_idx = cmd.index(ClaudeFlags.APPEND_SYSTEM_PROMPT)
        system_prompt = cmd[prompt_idx + 1]
        assert "load_recipe" in system_prompt
        assert "FIRST ACTION" in system_prompt


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
