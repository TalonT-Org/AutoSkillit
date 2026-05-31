"""Tests for doctor backend guard checks and process state breakdown."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


# ---------------------------------------------------------------------------
# REQ-DOCTOR-001 — _check_claude_process_state_breakdown
# ---------------------------------------------------------------------------


class TestCheckClaudeProcessStateBreakdown:
    """Tests for the claude_process_state doctor check (Check 15)."""

    def _ps_result(self, stdout: str, returncode: int = 0):
        return type(
            "CompletedProcess",
            (),
            {"returncode": returncode, "stdout": stdout},
        )()

    def test_ok_when_only_sleeping(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Single sleeping claude process → Severity.OK with state breakdown."""
        import subprocess

        from autoskillit.cli.doctor import Severity, _check_claude_process_state_breakdown

        header = "PID STAT %CPU COMMAND\n"
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: self._ps_result(header + "1234 S 0.5 claude"),
        )
        result = _check_claude_process_state_breakdown()
        assert result.severity == Severity.OK
        assert "S=1" in result.message

    def test_warns_on_d_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """claude process in D state → Severity.WARNING with pid and pcpu in message."""
        import subprocess

        from autoskillit.cli.doctor import Severity, _check_claude_process_state_breakdown

        header = "PID STAT %CPU COMMAND\n"
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: self._ps_result(header + "1234 D 99.0 claude"),
        )
        result = _check_claude_process_state_breakdown()
        assert result.severity == Severity.WARNING
        assert "D=1" in result.message
        assert "99.0" in result.message

    def test_ok_when_no_claude_processes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty ps output (no claude rows) → Severity.OK, 'No claude processes running'."""
        import subprocess

        from autoskillit.cli.doctor import Severity, _check_claude_process_state_breakdown

        header = "PID STAT %CPU COMMAND\n"
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: self._ps_result(header + "5678 S 0.1 python"),
        )
        result = _check_claude_process_state_breakdown()
        assert result.severity == Severity.OK
        assert result.message == "No claude processes running"

    def test_ok_when_ps_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FileNotFoundError from ps → Severity.OK explaining ps unavailability."""
        import subprocess

        from autoskillit.cli.doctor import Severity, _check_claude_process_state_breakdown

        def _raise(*a, **kw):
            raise FileNotFoundError("ps")

        monkeypatch.setattr(subprocess, "run", _raise)
        result = _check_claude_process_state_breakdown()
        assert result.severity == Severity.OK
        assert "ps unavailable" in result.message
        assert "FileNotFoundError" in result.message


class TestCheckStaleMcpServersBackendGuard:
    def test_non_claude_code_backend_returns_ok_skip(self) -> None:
        """Non-claude-code backend returns OK skip without filesystem access."""
        from autoskillit.cli.doctor._doctor_mcp import _check_stale_mcp_servers
        from autoskillit.core import Severity

        results = _check_stale_mcp_servers(backend="aider")
        assert len(results) == 1
        assert results[0].severity == Severity.OK
        assert results[0].check == "stale_mcp_servers"
        assert "skipped" in results[0].message.lower()

    def test_none_backend_preserves_existing_behavior(self, tmp_path: Path) -> None:
        """Default None backend does NOT skip — existing behavior intact."""
        from autoskillit.cli.doctor._doctor_mcp import _check_stale_mcp_servers
        from autoskillit.core import Severity

        claude_json = tmp_path / ".claude.json"
        claude_json.write_text('{"mcpServers": {}}')
        results = _check_stale_mcp_servers(claude_json_path=claude_json, backend=None)
        assert len(results) == 1
        assert results[0].severity == Severity.OK
        assert "skipped" not in results[0].message.lower()

    def test_claude_code_backend_preserves_existing_behavior(self, tmp_path: Path) -> None:
        """Explicit claude-code backend does NOT skip — existing behavior intact."""
        from autoskillit.cli.doctor._doctor_mcp import _check_stale_mcp_servers
        from autoskillit.core import Severity

        claude_json = tmp_path / ".claude.json"
        claude_json.write_text('{"mcpServers": {}}')
        results = _check_stale_mcp_servers(claude_json_path=claude_json, backend="claude-code")
        assert len(results) == 1
        assert results[0].severity == Severity.OK
        assert "skipped" not in results[0].message.lower()


class TestCheckMcpServerRegisteredBackendGuard:
    def test_non_claude_code_backend_returns_ok_skip(self) -> None:
        """Non-claude-code backend returns OK skip without filesystem/subprocess access."""
        from autoskillit.cli.doctor._doctor_mcp import _check_mcp_server_registered
        from autoskillit.core import Severity

        result = _check_mcp_server_registered(backend="aider")
        assert result.severity == Severity.OK
        assert result.check == "mcp_server_registered"
        assert "skipped" in result.message.lower()

    def test_none_backend_preserves_existing_behavior(self, tmp_path: Path) -> None:
        """Default None backend does NOT skip."""
        from autoskillit.cli.doctor._doctor_mcp import _check_mcp_server_registered
        from autoskillit.core import Severity

        claude_json = tmp_path / ".claude.json"
        claude_json.write_text('{"mcpServers": {"autoskillit": {"command": "x"}}}')
        result = _check_mcp_server_registered(claude_json_path=claude_json, backend=None)
        assert result.severity == Severity.OK
        assert "skipped" not in result.message.lower()

    def test_claude_code_backend_preserves_existing_behavior(self, tmp_path: Path) -> None:
        """Explicit claude-code backend does NOT skip."""
        from autoskillit.cli.doctor._doctor_mcp import _check_mcp_server_registered
        from autoskillit.core import Severity

        claude_json = tmp_path / ".claude.json"
        claude_json.write_text('{"mcpServers": {"autoskillit": {"command": "x"}}}')
        result = _check_mcp_server_registered(claude_json_path=claude_json, backend="claude-code")
        assert result.severity == Severity.OK
        assert "skipped" not in result.message.lower()


class TestCheckMcpServerRegisteredCodexBackend:
    """Tests for codex backend branch in _check_mcp_server_registered."""

    def test_codex_backend_ok_when_registered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """backend='codex' with valid registration returns OK."""
        from autoskillit.cli.doctor._doctor_mcp import _check_mcp_server_registered
        from autoskillit.core import (
            CODEX_MCP_ENV_FORWARD_VARS,
            HEADLESS_AUTO_GATE_ENV_VAR,
            Severity,
        )

        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        env_vars_str = ", ".join(
            f'"{v}"' for v in sorted(CODEX_MCP_ENV_FORWARD_VARS - {HEADLESS_AUTO_GATE_ENV_VAR})
        )
        (codex_dir / "config.toml").write_text(
            f'[mcp_servers.autoskillit]\ncommand = "autoskillit"\nenv_vars = [{env_vars_str}]\n'
        )
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        result = _check_mcp_server_registered(backend="codex")
        assert result.severity == Severity.OK
        assert result.check == "mcp_server_registered"
        assert "registered" in result.message.lower()
        assert "skipped" not in result.message.lower()

    def test_codex_backend_warning_when_not_registered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """backend='codex' with missing autoskillit entry returns WARNING."""
        from autoskillit.cli.doctor._doctor_mcp import _check_mcp_server_registered
        from autoskillit.core import Severity

        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "config.toml").write_text("")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        result = _check_mcp_server_registered(backend="codex")
        assert result.severity == Severity.WARNING
        assert result.check == "mcp_server_registered"
        assert "autoskillit init" in result.message

    def test_codex_backend_warning_when_config_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """backend='codex' with no config.toml file returns WARNING."""
        from autoskillit.cli.doctor._doctor_mcp import _check_mcp_server_registered
        from autoskillit.core import Severity

        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        result = _check_mcp_server_registered(backend="codex")
        assert result.severity == Severity.WARNING
        assert result.check == "mcp_server_registered"
        assert "autoskillit init" in result.message

    def test_codex_backend_warning_when_config_corrupt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """backend='codex' with corrupt TOML returns WARNING."""
        from autoskillit.cli.doctor._doctor_mcp import _check_mcp_server_registered
        from autoskillit.core import Severity

        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "config.toml").write_text("[[[bad toml")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        result = _check_mcp_server_registered(backend="codex")
        assert result.severity == Severity.WARNING
        assert result.check == "mcp_server_registered"


class TestCheckClaudeProcessStateBreakdownBackendGuard:
    def test_non_claude_code_backend_returns_ok_skip(self) -> None:
        """Non-claude-code backend returns OK skip without subprocess access."""
        from autoskillit.cli.doctor._doctor_runtime import _check_claude_process_state_breakdown
        from autoskillit.core import Severity

        result = _check_claude_process_state_breakdown(backend="aider")
        assert result.severity == Severity.OK
        assert result.check == "claude_process_state"
        assert "skipped" in result.message.lower()

    def test_none_backend_preserves_existing_behavior(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default None backend does NOT skip — runs ps as before."""
        import subprocess

        from autoskillit.cli.doctor._doctor_runtime import _check_claude_process_state_breakdown
        from autoskillit.core import Severity

        header = "PID STAT %CPU COMMAND\n"
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: type(
                "CP", (), {"returncode": 0, "stdout": header + "1234 S 0.5 claude"}
            )(),
        )
        result = _check_claude_process_state_breakdown(backend=None)
        assert result.severity == Severity.OK
        assert "skipped" not in result.message.lower()

    def test_claude_code_backend_preserves_existing_behavior(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit claude-code backend does NOT skip."""
        import subprocess

        from autoskillit.cli.doctor._doctor_runtime import _check_claude_process_state_breakdown
        from autoskillit.core import Severity

        header = "PID STAT %CPU COMMAND\n"
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: type(
                "CP", (), {"returncode": 0, "stdout": header + "1234 S 0.5 claude"}
            )(),
        )
        result = _check_claude_process_state_breakdown(backend="claude-code")
        assert result.severity == Severity.OK
        assert "skipped" not in result.message.lower()


class TestCheckClaudeProcessStateBreakdownCodexBackend:
    """Tests for codex backend branch in _check_claude_process_state_breakdown."""

    def _ps_result(self, stdout: str, returncode: int = 0):
        return type(
            "CompletedProcess",
            (),
            {"returncode": returncode, "stdout": stdout},
        )()

    def test_ok_when_only_sleeping_codex(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Single sleeping codex process → Severity.OK with state breakdown."""
        import subprocess

        from autoskillit.cli.doctor._doctor_runtime import _check_claude_process_state_breakdown
        from autoskillit.core import Severity

        header = "PID STAT %CPU COMMAND\n"
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: self._ps_result(header + "1234 S 0.5 codex"),
        )
        result = _check_claude_process_state_breakdown(backend="codex")
        assert result.severity == Severity.OK
        assert result.check == "codex_process_state"
        assert "S=1" in result.message

    def test_warns_on_d_state_codex(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """codex process in D state → Severity.WARNING with pid and pcpu."""
        import subprocess

        from autoskillit.cli.doctor._doctor_runtime import _check_claude_process_state_breakdown
        from autoskillit.core import Severity

        header = "PID STAT %CPU COMMAND\n"
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: self._ps_result(header + "1234 D 99.0 codex"),
        )
        result = _check_claude_process_state_breakdown(backend="codex")
        assert result.severity == Severity.WARNING
        assert result.check == "codex_process_state"
        assert "D=1" in result.message
        assert "99.0" in result.message
        assert "codex processes in D state" in result.message

    def test_ok_when_no_codex_processes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No codex rows in ps output → Severity.OK, 'No codex processes running'."""
        import subprocess

        from autoskillit.cli.doctor._doctor_runtime import _check_claude_process_state_breakdown
        from autoskillit.core import Severity

        header = "PID STAT %CPU COMMAND\n"
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: self._ps_result(header + "5678 S 0.1 python"),
        )
        result = _check_claude_process_state_breakdown(backend="codex")
        assert result.severity == Severity.OK
        assert result.check == "codex_process_state"
        assert result.message == "No codex processes running"

    def test_ok_when_ps_missing_codex(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FileNotFoundError from ps → Severity.OK explaining ps unavailability."""
        import subprocess

        from autoskillit.cli.doctor._doctor_runtime import _check_claude_process_state_breakdown
        from autoskillit.core import Severity

        def _raise(*a, **kw):
            raise FileNotFoundError("ps")

        monkeypatch.setattr(subprocess, "run", _raise)
        result = _check_claude_process_state_breakdown(backend="codex")
        assert result.severity == Severity.OK
        assert result.check == "codex_process_state"
        assert "ps unavailable" in result.message
        assert "FileNotFoundError" in result.message


class TestRunDoctorBackendWiring:
    def test_run_doctor_passes_backend_to_guarded_checks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """run_doctor passes cfg.agent_backend.backend to guarded checks."""
        from unittest.mock import patch

        from autoskillit.cli.doctor import run_doctor
        from autoskillit.cli.doctor._doctor_types import DoctorResult
        from autoskillit.core import Severity

        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)
        (tmp_path / ".autoskillit").mkdir()
        (tmp_path / ".autoskillit" / "config.yaml").write_text(
            "agent_backend:\n  backend: aider\n"
        )

        captured_backends: list[str | None] = []

        def _capture_stale(*args: object, **kwargs: object) -> list[DoctorResult]:
            captured_backends.append(kwargs.get("backend"))
            return [DoctorResult(Severity.OK, "stale_mcp_servers", "captured")]

        with patch("autoskillit.cli.doctor._check_stale_mcp_servers", side_effect=_capture_stale):
            run_doctor()

        assert captured_backends == ["aider"]


class TestDoctorCorruptConfigDetail:
    def test_doctor_reports_parse_error_not_just_unregistered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Corrupt TOML containing [mcp_servers.autoskillit] text → OK, not WARNING."""
        from autoskillit.cli.doctor._doctor_mcp import _check_mcp_server_registered
        from autoskillit.core import Severity

        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "config.toml").write_text(
            "[projects./home/user/repo]\ntrust = true\n\n"
            '[mcp_servers.autoskillit]\ncommand = "autoskillit"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        result = _check_mcp_server_registered(backend="codex")
        assert result.severity == Severity.OK
        assert result.check == "mcp_server_registered"


class TestCheckCodexVersion:
    """Tests for _check_codex_version doctor check (Check 30)."""

    def _codex_result(self, stdout: str, returncode: int = 0, stderr: str = ""):
        return type(
            "CompletedProcess",
            (),
            {"returncode": returncode, "stdout": stdout, "stderr": stderr},
        )()

    def test_skip_for_non_codex_backend(self) -> None:
        from autoskillit.cli.doctor._doctor_runtime import _check_codex_version
        from autoskillit.core import Severity

        result = _check_codex_version(backend="claude-code")
        assert result.severity == Severity.OK
        assert "skipped" in result.message.lower()

    def test_file_not_found_returns_ok_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        from autoskillit.cli.doctor._doctor_runtime import _check_codex_version
        from autoskillit.core import Severity

        def _raise(*a, **kw):
            raise FileNotFoundError("codex")

        monkeypatch.setattr(subprocess, "run", _raise)
        result = _check_codex_version()
        assert result.severity == Severity.OK
        assert "unavailable" in result.message.lower()

    def test_timeout_returns_ok_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        from autoskillit.cli.doctor._doctor_runtime import _check_codex_version
        from autoskillit.core import Severity

        def _raise(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="codex", timeout=5)

        monkeypatch.setattr(subprocess, "run", _raise)
        result = _check_codex_version()
        assert result.severity == Severity.OK
        assert "unavailable" in result.message.lower()

    def test_version_below_minimum_returns_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        from autoskillit.cli.doctor._doctor_runtime import _check_codex_version
        from autoskillit.core import Severity

        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: self._codex_result("Codex 0.129.0\n"),
        )
        result = _check_codex_version()
        assert result.severity == Severity.WARNING
        assert "0.129.0" in result.message
        assert "below minimum" in result.message

    def test_version_at_minimum_returns_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        from autoskillit.cli.doctor._doctor_runtime import _check_codex_version
        from autoskillit.core import Severity

        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: self._codex_result("Codex 0.130.0\n"),
        )
        result = _check_codex_version()
        assert result.severity == Severity.OK
        assert "0.130.0" in result.message

    def test_version_above_minimum_returns_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        from autoskillit.cli.doctor._doctor_runtime import _check_codex_version
        from autoskillit.core import Severity

        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: self._codex_result("Codex 0.131.0\n"),
        )
        result = _check_codex_version()
        assert result.severity == Severity.OK
        assert "0.131.0" in result.message
