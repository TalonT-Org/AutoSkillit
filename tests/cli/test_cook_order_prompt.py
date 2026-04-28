"""Tests: cook CLI order command — system prompt content, MCP prefix selection, display ownership."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit import cli
from autoskillit.core import ClaudeFlags

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]

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


class TestCLIOrderPrompt:
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

    @pytest.fixture(autouse=True)
    def _stub_ingredients_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stub _get_ingredients_table in app.py to prevent subprocess.run git calls."""
        import importlib
        import sys as _sys

        _app_mod = _sys.modules.get("autoskillit.cli.app") or importlib.import_module(
            "autoskillit.cli.app"
        )
        monkeypatch.setattr(_app_mod, "_get_ingredients_table", lambda *a, **kw: "| col | val |")

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

        input_calls = []
        monkeypatch.setattr("builtins.input", lambda prompt="": input_calls.append(prompt) or "")

        cli.order("test-script")

        assert len(input_calls) == 1, "input() should be called exactly once (confirmation)"

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

_PLUGIN_KEY = "autoskillit@autoskillit-local"


class TestOrderMcpPrefixSelection:
    """order() must embed the resolved MCP prefix in the system prompt."""

    @pytest.fixture(autouse=True)
    def _stub_preview(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("autoskillit.cli._prompts.show_cook_preview", lambda *a, **kw: None)

    @pytest.fixture(autouse=True)
    def _interactive_stdin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    @pytest.fixture(autouse=True)
    def _stub_ingredients_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stub _get_ingredients_table in app.py to prevent subprocess.run git calls."""
        import importlib
        import sys as _sys

        _app_mod = _sys.modules.get("autoskillit.cli.app") or importlib.import_module(
            "autoskillit.cli.app"
        )
        monkeypatch.setattr(_app_mod, "_get_ingredients_table", lambda *a, **kw: "| col | val |")

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
            "autoskillit.core._plugin_ids._installed_plugins_path", lambda: plugins_file
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
            "autoskillit.core._plugin_ids._installed_plugins_path", lambda: plugins_file
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

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_passes_ingredients_table_to_orchestrator_prompt(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cook() must pass the pre-rendered ingredients_table to _build_orchestrator_prompt."""
        captured: list[dict] = []

        def _capturing_build(
            recipe_name: str, mcp_prefix: str, ingredients_table: object = None
        ) -> str:
            captured.append({"ingredients_table": ingredients_table})
            return "ROUTING RULES\nFIRST ACTION\nopenKitchen\nDuring pipeline execution"

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
            "autoskillit.core._plugin_ids._installed_plugins_path", lambda: plugins_file
        )
        import importlib
        import sys as _sys

        _app_mod = _sys.modules.get("autoskillit.cli.app") or importlib.import_module(
            "autoskillit.cli.app"
        )
        monkeypatch.setattr(_app_mod, "_build_orchestrator_prompt", _capturing_build)
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.order("test-script")

        assert captured, "_build_orchestrator_prompt was not called"
        assert captured[0]["ingredients_table"] == "| col | val |", (
            "app.cook() must pass pre-rendered ingredients_table to _build_orchestrator_prompt"
        )
