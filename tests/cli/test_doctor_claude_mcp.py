from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.cli.doctor._doctor_mcp import _check_claude_mcp_timeouts
from autoskillit.config._config_dataclasses import RunSkillConfig
from autoskillit.core import Severity

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


class TestCheckClaudeMcpTimeouts:
    def _claude_backend(self):
        from autoskillit.execution.backends.claude import ClaudeCodeBackend

        return ClaudeCodeBackend()

    def _write_claude_json(self, tmp_path: Path, monkeypatch, entry: dict | None) -> None:
        claude_json = tmp_path / ".claude.json"
        data = {"mcpServers": {"autoskillit": entry}} if entry is not None else {"mcpServers": {}}
        claude_json.write_text(json.dumps(data))
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

    def test_detects_stale_timeout(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._write_claude_json(tmp_path, monkeypatch, {"command": "autoskillit", "timeout": 5000})
        result = _check_claude_mcp_timeouts(
            backend=self._claude_backend(), run_skill=RunSkillConfig()
        )
        assert result.severity == Severity.WARNING
        assert result.check == "claude_mcp_timeouts"
        assert "5000" in result.message

    def test_warns_when_timeout_field_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._write_claude_json(tmp_path, monkeypatch, {"command": "autoskillit"})
        result = _check_claude_mcp_timeouts(
            backend=self._claude_backend(), run_skill=RunSkillConfig()
        )
        assert result.severity == Severity.WARNING
        assert result.check == "claude_mcp_timeouts"

    def test_ok_when_timeout_sufficient(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rs = RunSkillConfig()
        expected_ms = int(rs.mcp_tool_timeout_sec * 1000)
        # observed == expected must return OK (the check uses strict `<`,
        # so the inclusive boundary is the highest-value point to pin).
        self._write_claude_json(
            tmp_path,
            monkeypatch,
            {"command": "autoskillit", "timeout": expected_ms},
        )
        result = _check_claude_mcp_timeouts(backend=self._claude_backend(), run_skill=rs)
        assert result.severity == Severity.OK
        self._write_claude_json(
            tmp_path,
            monkeypatch,
            {"command": "autoskillit", "timeout": expected_ms - 1},
        )
        result = _check_claude_mcp_timeouts(backend=self._claude_backend(), run_skill=rs)
        assert result.severity == Severity.WARNING

    def test_ok_when_no_direct_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No direct mcpServers entry (plugin-based install) — skip, not warn."""
        self._write_claude_json(tmp_path, monkeypatch, None)
        result = _check_claude_mcp_timeouts(
            backend=self._claude_backend(), run_skill=RunSkillConfig()
        )
        assert result.severity == Severity.OK

    def test_skipped_for_codex_backend(self) -> None:
        from autoskillit.execution.backends.codex import CodexBackend

        result = _check_claude_mcp_timeouts(backend=CodexBackend())
        assert result.severity == Severity.OK
        assert "skipped" in result.message.lower()

    def test_skipped_for_no_backend(self) -> None:
        result = _check_claude_mcp_timeouts(backend=None)
        assert result.severity == Severity.OK
        assert "skipped" in result.message.lower()
