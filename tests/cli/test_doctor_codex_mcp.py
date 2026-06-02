from __future__ import annotations

from types import SimpleNamespace

import pytest

from autoskillit.cli.doctor._doctor_mcp import _check_mcp_server_registered
from autoskillit.core import ReadResult, Severity

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

    def test_non_codex_backend_skip(self) -> None:
        _stub = SimpleNamespace(
            name="stub-no-mcp",
            capabilities=SimpleNamespace(mcp_config_capable=False),
        )
        result = _check_mcp_server_registered(backend=_stub)
        assert result.severity == Severity.OK
        assert "skipped" not in result.message.lower()
