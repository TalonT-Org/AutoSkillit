from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from autoskillit.cli.doctor._doctor_mcp import (
    _check_codex_mcp_timeouts,
    _check_mcp_server_registered,
)
from autoskillit.core import ReadResult, Severity
from autoskillit.execution.backends._codex_config import (
    CODEX_MCP_STARTUP_TIMEOUT_SEC,
    CODEX_MCP_TOOL_TIMEOUT_FLOOR,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


class TestCheckMcpServerRegisteredCodexBranch:
    def test_ok_when_valid_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import autoskillit.execution as _exec_mod
        from autoskillit.core import CODEX_MCP_ENV_FORWARD_VARS, HEADLESS_AUTO_GATE_ENV_VAR
        from autoskillit.execution.backends.codex import CodexBackend

        monkeypatch.setattr(
            _exec_mod,
            "_read_codex_config",
            lambda path: ReadResult.ok(
                {
                    "mcp_servers": {
                        "autoskillit": {
                            "command": "autoskillit",
                            "env_vars": sorted(
                                CODEX_MCP_ENV_FORWARD_VARS - {HEADLESS_AUTO_GATE_ENV_VAR}
                            ),
                            "startup_timeout_sec": CODEX_MCP_STARTUP_TIMEOUT_SEC,
                            "tool_timeout_sec": CODEX_MCP_TOOL_TIMEOUT_FLOOR,
                        }
                    }
                }
            ),
        )
        result = _check_mcp_server_registered(backend=CodexBackend())
        assert result.severity == Severity.OK

    def test_warning_when_missing_entry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import autoskillit.execution as _exec_mod
        from autoskillit.execution.backends.codex import CodexBackend

        monkeypatch.setattr(
            _exec_mod,
            "_read_codex_config",
            lambda path: ReadResult.ok({"mcp_servers": {}}),
        )
        result = _check_mcp_server_registered(backend=CodexBackend())
        assert result.severity == Severity.WARNING

    def test_warning_when_absent_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import autoskillit.execution as _exec_mod
        from autoskillit.execution.backends.codex import CodexBackend

        monkeypatch.setattr(
            _exec_mod,
            "_read_codex_config",
            lambda path: ReadResult.missing({}),
        )
        result = _check_mcp_server_registered(backend=CodexBackend())
        assert result.severity == Severity.WARNING

    def test_non_codex_backend_skip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".claude.json").write_text(
            '{"mcpServers": {"autoskillit": {"command": "autoskillit"}}}'
        )
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        _stub = SimpleNamespace(
            name="stub-no-mcp",
            capabilities=SimpleNamespace(mcp_config_capable=False),
        )
        result = _check_mcp_server_registered(backend=_stub)
        assert result.severity == Severity.OK
        assert result.check == "mcp_server_registered"
        assert "skipped" not in result.message.lower()


class TestCheckCodexMcpTimeouts:
    def _codex_backend(self):
        from autoskillit.execution.backends.codex import CodexBackend

        return CodexBackend()

    def test_detects_stale_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import autoskillit.execution as _exec_mod

        monkeypatch.setattr(
            _exec_mod,
            "_read_codex_config",
            lambda path: ReadResult.ok(
                {
                    "mcp_servers": {
                        "autoskillit": {
                            "command": "autoskillit",
                            "tool_timeout_sec": 120.0,
                        }
                    }
                }
            ),
        )
        result = _check_codex_mcp_timeouts(backend=self._codex_backend())
        assert result.severity == Severity.WARNING
        assert result.check == "codex_mcp_timeouts"
        assert "120.0" in result.message

    def test_ok_when_timeout_sufficient(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import autoskillit.execution as _exec_mod

        monkeypatch.setattr(
            _exec_mod,
            "_read_codex_config",
            lambda path: ReadResult.ok(
                {
                    "mcp_servers": {
                        "autoskillit": {
                            "command": "autoskillit",
                            "tool_timeout_sec": CODEX_MCP_TOOL_TIMEOUT_FLOOR,
                        }
                    }
                }
            ),
        )
        result = _check_codex_mcp_timeouts(backend=self._codex_backend())
        assert result.severity == Severity.OK

    def test_skipped_for_non_codex_backend(self) -> None:
        result = _check_codex_mcp_timeouts(backend=None)
        assert result.severity == Severity.OK
        assert "skipped" in result.message.lower()
