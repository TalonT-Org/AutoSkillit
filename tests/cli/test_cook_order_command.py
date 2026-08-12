"""Tests: cook CLI order command — script validation, command building, env injection."""

from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit import cli
from autoskillit.core import ClaudeFlags
from tests.cli.conftest import _SCRIPT_YAML

pytestmark = [
    pytest.mark.layer("cli"),
    pytest.mark.medium,
    pytest.mark.usefixtures("_stub_interactive_prelaunch"),
]


class TestCLIOrderCommand:
    @pytest.fixture(autouse=True)
    def _stub_preview(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Stub terminal preview to avoid subprocess.run collision with git calls."""
        monkeypatch.setattr(
            "autoskillit.cli._preview.show_cook_preview",
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

        _app_mod = _sys.modules.get(
            "autoskillit.cli.session._session_order"
        ) or importlib.import_module("autoskillit.cli.session._session_order")
        monkeypatch.setattr(_app_mod, "_get_ingredients_table", lambda *a, **kw: "| col | val |")

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
        assert "claude" in captured.err.lower()

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
        assert Path(cmd[0]).is_absolute()
        assert Path(cmd[0]).name == "claude"
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
    def test_order_includes_plugin_dir_when_plugin_installed(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order still passes --plugin-dir when a marketplace plugin is installed —
        EXPLICIT_PLUGIN_DIR generation-store binding is unconditional for a
        plugin-install-capable backend (IMPLICIT_INSTALLED was retired in #4480)."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        from autoskillit.core._plugin_ids import MARKETPLACE_PREFIX

        monkeypatch.setattr(
            "autoskillit.core.detect_autoskillit_mcp_prefix",
            lambda _capabilities: MARKETPLACE_PREFIX,
        )
        from autoskillit import __version__
        from autoskillit.cli._plugin_artifact import (
            current_installed_plugin_root,
            installed_plugin_semantic_key,
            publish_installed_plugin_artifact,
        )
        from autoskillit.core import _AUTOSKILLIT_PLUGIN_KEY

        installed_root = current_installed_plugin_root()
        installed_root.mkdir(parents=True)
        (installed_root / "plugin.json").write_text("{}\n", encoding="utf-8")
        metadata = installed_root / ".claude-plugin" / "plugin.json"
        metadata.parent.mkdir(parents=True)
        metadata.write_text(
            json.dumps({"name": "autoskillit", "version": __version__}),
            encoding="utf-8",
        )
        publish_installed_plugin_artifact(
            installed_root,
            semantic_key=installed_plugin_semantic_key(
                _AUTOSKILLIT_PLUGIN_KEY,
                __version__,
            ),
        )
        registry = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(
            json.dumps(
                {
                    "version": 2,
                    "plugins": {_AUTOSKILLIT_PLUGIN_KEY: {"installPath": str(installed_root)}},
                }
            ),
            encoding="utf-8",
        )
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.order("test-script")

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        # After the generation-keyed publication migration (#4480), all
        # bindings carry a concrete plugin_dir, so --plugin-dir is always
        # passed — even when the marketplace registry has an entry.
        assert ClaudeFlags.PLUGIN_DIR in cmd

    @patch("autoskillit.cli.subprocess.run")
    def test_order_includes_plugin_dir_when_no_plugin_installed(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order includes --plugin-dir when marketplace plugin is not installed."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        from autoskillit.core._plugin_ids import DIRECT_PREFIX

        monkeypatch.setattr(
            "autoskillit.core.detect_autoskillit_mcp_prefix",
            lambda _capabilities: DIRECT_PREFIX,
        )
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.order("test-script")

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert ClaudeFlags.PLUGIN_DIR in cmd

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
    def test_order_launch_sets_session_type_order(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order() passes AUTOSKILLIT_SESSION_TYPE=order to subprocess env."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "test-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        with patch("autoskillit.core.write_registry_entry"):
            cli.order("test-script")

        env = mock_run.call_args[1].get("env") or {}
        assert env.get("AUTOSKILLIT_SESSION_TYPE") == "orchestrator"

    @patch("autoskillit.cli.subprocess.run")
    def test_order_launch_sets_launch_id_env(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order() passes AUTOSKILLIT_LAUNCH_ID in subprocess env."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "test-script.yaml").write_text(_SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        with patch("autoskillit.core.write_registry_entry"):
            cli.order("test-script")

        env = mock_run.call_args[1].get("env") or {}
        assert "AUTOSKILLIT_LAUNCH_ID" in env

    @pytest.mark.parametrize("backend_name", ["claude-code", "codex"])
    def test_order_backend_produces_valid_command(
        self,
        backend_name: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """order() produces a valid command for each registered backend."""
        from autoskillit.core import CookSessionHandle
        from autoskillit.execution.backends import get_backend as _real_get_backend
        from autoskillit.execution.backends.codex import CodexBackend, CodexFlags

        real_backend = _real_get_backend(backend_name)
        captured: dict = {}

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(_SCRIPT_YAML)
        fake_binary = tmp_path / real_backend.binary_name()
        fake_binary.write_text("#!/bin/sh\nexit 0\n")
        fake_binary.chmod(0o755)
        monkeypatch.setattr(shutil, "which", lambda _: str(fake_binary))
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")

        mock_config = MagicMock()
        mock_config.agent_backend.backend = backend_name
        mock_config.features = {"codex_backend": True}
        mock_config.experimental_enabled = True
        mock_config.providers.profiles = {}
        mock_config.subsets.disabled = []
        mock_config.packs.enabled = []
        mock_config.branching.default_base_branch = "develop"
        mock_config.workspace.temp_dir = ".autoskillit/temp"
        monkeypatch.setattr("autoskillit.config.load_config", lambda *_a, **_kw: mock_config)
        monkeypatch.setattr(
            "autoskillit.cli.session._session_backend.resolve_global_backend",
            lambda name: _real_get_backend(name),
        )

        def fake_pre_launch(_self, *, session_dir=None, executable=None, plugin_dir=None):
            del executable, plugin_dir
            if session_dir is not None:
                session_dir.mkdir(parents=True, exist_ok=True)
                (session_dir / "config.toml").write_text(
                    '[mcp_servers.autoskillit]\ncommand = "autoskillit"\nargs = ["mcp"]\n'
                )
            return []

        monkeypatch.setattr(CodexBackend, "ensure_pre_launch", fake_pre_launch)
        monkeypatch.setattr(CodexBackend, "validate_interactive_invocation", lambda *_: [])
        monkeypatch.setattr(
            CodexBackend,
            "cook_session_context",
            lambda _self, **_kwargs: nullcontext(
                CookSessionHandle(
                    view_id="test-view",
                    pass_fds=(),
                    _record_spawn=lambda _pid, _pgid: None,
                    _record_reaped=lambda _pid, _pgid: None,
                )
            ),
        )

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-canary-key")

        def fake_run(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["env"] = kwargs.get("env", {}) or {}
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr(subprocess, "run", fake_run)

        def fake_cook_attempt(spec, **kwargs):
            captured["cmd"] = list(spec.cmd)
            captured["env"] = dict(spec.env)
            kwargs["on_spawn"](12345, 12345)
            kwargs["on_reaped"](12345, 12345)
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr(
            "autoskillit.cli.session._session_process.run_cook_attempt",
            fake_cook_attempt,
        )

        cli.order("test-script")

        assert "cmd" in captured, (
            "cli.order() did not invoke subprocess.run — check for early exit"
        )
        cmd = captured["cmd"]
        env = captured["env"]

        assert Path(cmd[0]).is_absolute()
        assert Path(cmd[0]).name == real_backend.binary_name(), (
            f"Expected {real_backend.binary_name()!r}, got {Path(cmd[0]).name!r}"
        )

        claude_flag_values = {str(f) for f in ClaudeFlags}
        codex_flag_values = {str(f) for f in CodexFlags}
        claude_only = claude_flag_values - codex_flag_values
        codex_only = codex_flag_values - claude_flag_values

        if backend_name == "codex":
            assert set(cmd).isdisjoint(claude_only), (
                f"Claude-only flags found in codex command: {set(cmd) & claude_only}"
            )
            assert "ANTHROPIC_API_KEY" not in env, "Codex env must strip ANTHROPIC_API_KEY"
        else:
            assert set(cmd).isdisjoint(codex_only), (
                f"Codex-only flags found in claude command: {set(cmd) & codex_only}"
            )


# ---------------------------------------------------------------------------
# ORDER_INTERACTIVE_REQUIRED_ENV call-site contract (issue #4253 Part A)
# ---------------------------------------------------------------------------

_SESSION_ORDER_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "autoskillit"
    / "cli"
    / "session"
    / "_session_order.py"
)


def _launch_cook_session_calls() -> list:
    """AST Call nodes for every _launch_cook_session(...) invocation in _session_order.py."""
    import ast

    tree = ast.parse(_SESSION_ORDER_PATH.read_text())
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_launch_cook_session"
    ]


def test_session_order_has_exactly_three_launch_cook_session_call_sites() -> None:
    calls = _launch_cook_session_calls()
    assert len(calls) == 3, (
        f"Expected exactly 3 _launch_cook_session() call sites in _session_order.py, "
        f"found {len(calls)}"
    )


def test_session_order_launch_calls_pass_order_interactive_required_env() -> None:
    """Every _launch_cook_session() call site must pass required_env=ORDER_INTERACTIVE_REQUIRED_ENV
    exactly — accepting any keyword value would allow None and would not protect the contract."""
    import ast

    for call in _launch_cook_session_calls():
        required_env_kwargs = [kw for kw in call.keywords if kw.arg == "required_env"]
        assert len(required_env_kwargs) == 1, (
            f"Call at line {call.lineno} must pass required_env= exactly once"
        )
        value = required_env_kwargs[0].value
        assert isinstance(value, ast.Name) and value.id == "ORDER_INTERACTIVE_REQUIRED_ENV", (
            f"Call at line {call.lineno} must pass required_env=ORDER_INTERACTIVE_REQUIRED_ENV, "
            f"got {ast.dump(value)}"
        )


def test_session_order_launch_calls_pass_explicit_home_authorities() -> None:
    required = {
        "skill_compilation",
        "launch_id",
        "default_base_branch",
        "workspace_temp_dir",
    }
    for call in _launch_cook_session_calls():
        supplied = {keyword.arg for keyword in call.keywords}
        assert required <= supplied, (
            f"Call at line {call.lineno} is missing {sorted(required - supplied)}"
        )


def test_launch_cook_session_required_env_is_required_keyword_only() -> None:
    """required_env must be a keyword-only parameter with no default — omission must be
    a language/type-checking error, not silently accepted as None."""
    import inspect

    from autoskillit.cli.session._session_launch import _launch_cook_session

    sig = inspect.signature(_launch_cook_session)
    param = sig.parameters["required_env"]
    assert param.kind == inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty
