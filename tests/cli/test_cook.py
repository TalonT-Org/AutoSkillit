"""Tests for CLI order, workspace, and skills list commands."""

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
        r2 = self._make_recipe("remediation")
        assert _resolve_recipe_input("1", [r1, r2]) is r1

    def test_valid_number_last_returns_last_recipe(self) -> None:
        r1 = self._make_recipe("implementation")
        r2 = self._make_recipe("remediation")
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


class TestCLIOrder:
    @pytest.fixture(autouse=True)
    def _stub_preview(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stub terminal preview to avoid subprocess.run collision with git calls."""
        monkeypatch.setattr(
            "autoskillit.cli._prompts.show_cook_preview",
            lambda *a, **kw: None,
        )

    @pytest.fixture(autouse=True)
    def _interactive_stdin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Most order() paths require an interactive TTY — default to True for this class."""
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)

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

    # --- order ---

    def test_order_blocked_inside_claude_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """order exits 1 when CLAUDECODE env var is set."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CLAUDECODE", "1")
        with pytest.raises(SystemExit) as exc_info:
            cli.order("any-script")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "regular terminal" in captured.out.lower()

    def test_order_script_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """order exits 1 when script name doesn't match any entry."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)

        with pytest.raises(SystemExit) as exc_info:
            cli.order("nonexistent")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "nonexistent" in captured.out

    def test_order_no_scripts_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """order exits 1 with available bundled recipes listed when name not found."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            cli.order("anything")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Recipe not found: 'anything'" in captured.out
        assert "Available recipes:" in captured.out
        assert "implementation" in captured.out

    def test_order_available_scripts_listed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """order lists available scripts when name doesn't match."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)

        with pytest.raises(SystemExit) as exc_info:
            cli.order("nonexistent")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Available recipes:" in captured.out
        assert "test-script" in captured.out

    def test_order_claude_not_on_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """order exits 1 when claude command is not found."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: None)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")

        with pytest.raises(SystemExit) as exc_info:
            cli.order("test-script")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "claude" in captured.out.lower()

    def test_order_invalid_script_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """order exits 1 when script YAML fails validation."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "bad-script.yaml").write_text("name: bad-script\nsteps: {}\n")

        with pytest.raises(SystemExit) as exc_info:
            cli.order("bad-script")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "validation" in captured.out.lower() or "error" in captured.out.lower()

    @patch("autoskillit.cli.subprocess.run")
    def test_order_builds_correct_command(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order passes correct flags to subprocess.run."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.order("test-script")

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
        assert ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS in cmd
        assert ClaudeFlags.PRINT not in cmd
        assert ClaudeFlags.ALLOW_DANGEROUSLY_SKIP_PERMISSIONS not in cmd
        kwargs = mock_run.call_args[1] if mock_run.call_args[1] else {}
        assert "capture_output" not in kwargs
        assert "stdin" not in kwargs

    @patch("autoskillit.cli.subprocess.run")
    def test_order_system_prompt_contains_behavioral_instructions(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order injects recipe name and behavioral instructions into system prompt."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.order("test-script")

        cmd = mock_run.call_args[0][0]
        prompt_idx = cmd.index(ClaudeFlags.APPEND_SYSTEM_PROMPT)
        system_prompt = cmd[prompt_idx + 1]
        assert "test-script" in system_prompt
        assert "open_kitchen" in system_prompt
        assert "ROUTING RULES" in system_prompt
        assert "FAILURE PREDICATES" in system_prompt
        assert "capture:" in system_prompt
        assert "${{ context." in system_prompt
        assert "AutoSkillit MCP tools" in system_prompt
        assert "--- RECIPE ---" not in system_prompt
        assert "do-something" not in system_prompt

    @patch("autoskillit.cli.subprocess.run")
    def test_orchestrator_prompt_contains_context_limit_routing(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Orchestrator prompt must instruct routing to on_context_limit when needs_retry=true."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.order("test-script")

        cmd = mock_run.call_args[0][0]
        prompt_idx = cmd.index(ClaudeFlags.APPEND_SYSTEM_PROMPT)
        system_prompt = cmd[prompt_idx + 1]
        assert "needs_retry" in system_prompt
        assert "on_context_limit" in system_prompt

    @patch("autoskillit.cli.subprocess.run")
    def test_order_propagates_exit_code(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order does not raise SystemExit on returncode 0."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.order("test-script")  # should not raise
        mock_run.assert_called_once()

    @patch("autoskillit.cli.subprocess.run")
    def test_order_subprocess_failure_propagates(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order propagates non-zero subprocess exit codes."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=42, stdout="", stderr=""
        )

        with pytest.raises(SystemExit) as exc_info:
            cli.order("test-script")
        assert exc_info.value.code == 42

    @patch("autoskillit.cli.subprocess.run")
    def test_order_uses_dangerously_skip_permissions(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order passes --dangerously-skip-permissions to claude."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.order("test-script")

        cmd = mock_run.call_args[0][0]
        assert ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS in cmd

    def test_order_recipe_not_found_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """order exits 1 when the given recipe name is not found."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            cli.order("totally-unknown-recipe-xyz")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "totally-unknown-recipe-xyz" in captured.out

    def test_order_malformed_yaml_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """order exits 1 with YAML parse error message when load_recipe raises YAMLError."""
        from autoskillit.core import YAMLError

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        with (
            patch("autoskillit.recipe.find_recipe_by_name", return_value=MagicMock()),
            patch("autoskillit.recipe.load_recipe", side_effect=YAMLError("bad yaml")),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli.order("bad-recipe")

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "YAML parse error" in captured.out

    def test_order_structure_error_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """order exits 1 with structure error message when load_recipe raises ValueError."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        with (
            patch("autoskillit.recipe.find_recipe_by_name", return_value=MagicMock()),
            patch("autoskillit.recipe.load_recipe", side_effect=ValueError("bad structure")),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli.order("bad-recipe")

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "structure error" in captured.out

    @patch("autoskillit.cli.subprocess.run")
    def test_order_named_recipe_only_confirmation_prompt(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order only fires confirmation prompt (no picker)."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        prompts_seen: list[str] = []
        monkeypatch.setattr("builtins.input", lambda prompt="": prompts_seen.append(prompt) or "")

        cli.order("test-script")

        assert len(prompts_seen) == 1, "input() should be called exactly once (confirmation)"
        assert "Launch session" in prompts_seen[0]

    @patch("autoskillit.cli.subprocess.run")
    def test_order_command_includes_positional_greeting(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The order command must pass a greeting as a positional argument."""
        from autoskillit.cli._prompts import _COOK_GREETINGS

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.order("test-script")

        cmd = mock_run.call_args[0][0]
        greeting_candidates = [g.format(recipe_name="test-script") for g in _COOK_GREETINGS]
        assert any(arg in greeting_candidates for arg in cmd), (
            f"No greeting found as positional arg in: {cmd}"
        )

    @patch("autoskillit.cli.subprocess.run")
    def test_order_no_recipe_prompts_user(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order prompts for recipe name when none is provided."""
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

        cli.order()  # no recipe argument

        mock_run.assert_called_once()

    def test_order_no_recipe_no_available_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """order exits 1 when no recipe is given and no recipes are available."""
        from unittest.mock import MagicMock as _MagicMock

        import autoskillit.recipe as _recipe_mod

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        mock_result = _MagicMock()
        mock_result.items = []
        monkeypatch.setattr(_recipe_mod, "list_recipes", lambda _: mock_result)

        with pytest.raises(SystemExit) as exc_info:
            cli.order()
        assert exc_info.value.code == 1

    @patch("autoskillit.cli.subprocess.run")
    def test_order_picker_shows_zero_option(
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

        cli.order()

        captured = capsys.readouterr()
        assert "0. Open kitchen" in captured.out

    @patch("autoskillit.cli.subprocess.run")
    def test_order_picker_zero_launches_open_kitchen(
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

        cli.order()

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        system_prompt_idx = cmd.index(ClaudeFlags.APPEND_SYSTEM_PROMPT) + 1
        assert "--- RECIPE ---" not in cmd[system_prompt_idx]

    def test_order_picker_out_of_range_exits(
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
            cli.order()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Invalid selection" in captured.out

    @patch("autoskillit.cli.subprocess.run")
    def test_order_open_kitchen_includes_positional_greeting(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Open-kitchen order sessions also pass a greeting as positional arg."""
        from autoskillit.cli._prompts import _OPEN_KITCHEN_GREETINGS

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "0")

        cli.order()

        cmd = mock_run.call_args[0][0]
        assert any(arg in _OPEN_KITCHEN_GREETINGS for arg in cmd), (
            f"No open-kitchen greeting found as positional arg in: {cmd}"
        )

    @patch("autoskillit.cli.subprocess.run")
    def test_order_resume_discovered_session_id_in_subprocess_cmd(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order(resume=True) discovers session via find_latest_session_id and passes --resume."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        with patch("autoskillit.core.find_latest_session_id", return_value="sess_abc"):
            cli.order("test-script", resume=True)

        cmd = mock_run.call_args[0][0]
        assert "--resume" in cmd
        assert cmd[cmd.index("--resume") + 1] == "sess_abc"

    @patch("autoskillit.cli.subprocess.run")
    def test_order_resume_explicit_session_id_skips_discovery(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order(resume=True, session_id='explicit-abc') uses explicit id; discovery not called."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        discovery_calls: list = []

        def fake_discover(cwd=None):
            discovery_calls.append(cwd)
            return "should-not-be-used"

        with patch("autoskillit.core.find_latest_session_id", side_effect=fake_discover):
            cli.order("test-script", resume=True, session_id="explicit-abc")

        cmd = mock_run.call_args[0][0]
        assert "--resume" in cmd
        assert cmd[cmd.index("--resume") + 1] == "explicit-abc"
        assert not discovery_calls, (
            "find_latest_session_id must not be called when session_id is explicit"
        )

    @patch("autoskillit.cli.subprocess.run")
    def test_order_resume_no_prior_session_starts_fresh(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order(resume=True) with no prior session omits --resume from subprocess command."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        with patch("autoskillit.core.find_latest_session_id", return_value=None):
            cli.order("test-script", resume=True)

        cmd = mock_run.call_args[0][0]
        assert "--resume" not in cmd


class TestOrderDisplayOwnership:
    """order() delegates recipe display to the Claude session via load_recipe."""

    @pytest.fixture(autouse=True)
    def _stub_preview(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stub terminal preview to avoid subprocess.run collision with git calls."""
        monkeypatch.setattr(
            "autoskillit.cli._prompts.show_cook_preview",
            lambda *a, **kw: None,
        )

    def _setup_recipe(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Write test recipe to scripts_dir and chdir."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        return scripts_dir

    @patch("autoskillit.cli.subprocess.run")
    def test_order_system_prompt_does_not_contain_recipe_yaml(
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

        cli.order("test-script")

        cmd = mock_run.call_args[0][0]
        prompt_idx = cmd.index(ClaudeFlags.APPEND_SYSTEM_PROMPT)
        system_prompt = cmd[prompt_idx + 1]
        assert "test-script" in system_prompt
        assert "--- RECIPE ---" not in system_prompt
        assert "--- END RECIPE ---" not in system_prompt
        assert "steps:" not in system_prompt
        assert "on_success:" not in system_prompt

    @patch("autoskillit.cli.subprocess.run")
    def test_order_system_prompt_instructs_open_kitchen_with_recipe(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """System prompt must instruct Claude to call open_kitchen(name) as its first action."""
        self._setup_recipe(tmp_path, monkeypatch)
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.order("test-script")

        cmd = mock_run.call_args[0][0]
        prompt_idx = cmd.index(ClaudeFlags.APPEND_SYSTEM_PROMPT)
        system_prompt = cmd[prompt_idx + 1]
        assert "open_kitchen" in system_prompt
        assert "FIRST ACTION" in system_prompt


_GITHUB_RECIPE_YAML = """\
name: github-recipe
description: A recipe using github tools
summary: Fetch an issue
steps:
  fetch:
    tool: fetch_github_issue
    with:
      issue_url: https://github.com/example/repo/issues/1
    on_success: done
    on_failure: done
  done:
    action: stop
    message: Done
kitchen_rules:
  - Only use AutoSkillit MCP tools during pipeline execution
"""


class TestOrderSubsetGate:
    """Tests for the order-time subset-disabled gate (T-VAL-008..010)."""

    @pytest.fixture(autouse=True)
    def _stub_preview(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "autoskillit.cli._prompts.show_cook_preview",
            lambda *a, **kw: None,
        )

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
        # "1" = enable temporarily, "" = confirm launch
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
        # "2" = enable permanently, then "n" = cancel launch (to avoid needing subprocess)
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
        cli.recipes_render(None)  # list all
        captured = capsys.readouterr()
        assert captured.out.strip(), "Expected recipe names in output"
        assert "implementation" in captured.out


_PLUGIN_KEY = "autoskillit@autoskillit-local"


class TestOrderMcpPrefixSelection:
    """order() must embed the resolved MCP prefix in the system prompt."""

    @pytest.fixture(autouse=True)
    def _stub_preview(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("autoskillit.cli._prompts.show_cook_preview", lambda *a, **kw: None)

    @pytest.fixture(autouse=True)
    def _interactive_stdin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    @patch("autoskillit.cli.subprocess.run")
    def test_order_prompt_uses_direct_prefix_when_no_marketplace_install(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """order() builds a prompt with the direct prefix when installed_plugins.json lacks key."""
        from autoskillit.cli._mcp_names import DIRECT_PREFIX

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "test-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        plugins_file = tmp_path / "plugins.json"
        plugins_file.write_text('{"version": 2, "plugins": {}}')
        monkeypatch.setattr(
            "autoskillit.cli._mcp_names._installed_plugins_path", lambda: plugins_file
        )
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.order("test-script")

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        prompt_idx = cmd.index(ClaudeFlags.APPEND_SYSTEM_PROMPT)
        captured_prompt = cmd[prompt_idx + 1]
        assert f"{DIRECT_PREFIX}open_kitchen" in captured_prompt

    @patch("autoskillit.cli.subprocess.run")
    def test_order_prompt_uses_marketplace_prefix_when_plugin_installed(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """order() uses marketplace prefix when autoskillit is plugin-installed."""
        from autoskillit.cli._mcp_names import MARKETPLACE_PREFIX

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "test-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        plugins_file = tmp_path / "plugins.json"
        plugins_file.write_text(f'{{"version": 2, "plugins": {{"{_PLUGIN_KEY}": []}}}}')
        monkeypatch.setattr(
            "autoskillit.cli._mcp_names._installed_plugins_path", lambda: plugins_file
        )
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.order("test-script")

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        prompt_idx = cmd.index(ClaudeFlags.APPEND_SYSTEM_PROMPT)
        captured_prompt = cmd[prompt_idx + 1]
        assert f"{MARKETPLACE_PREFIX}open_kitchen" in captured_prompt
