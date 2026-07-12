"""Tests for the _check_claude_binary doctor check."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from autoskillit.cli.doctor._doctor_runtime import _check_claude_binary
from autoskillit.core import Severity

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


class TestCheckClaudeBinary:
    """Doctor check for claude CLI binary availability (capability-driven rerouting)."""

    def test_passes_when_claude_binary_on_path(self) -> None:
        """_check_claude_binary returns OK when claude is found on PATH."""
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            result = _check_claude_binary()

        assert result.severity == Severity.OK
        assert result.check == "claude_binary"

    def test_warns_when_claude_binary_missing(self) -> None:
        """_check_claude_binary returns WARNING when claude is not on PATH."""
        with patch("shutil.which", return_value=None):
            result = _check_claude_binary()

        assert result.severity == Severity.WARNING
        assert result.check == "claude_binary"
        msg = result.message.lower()
        assert "not found" in msg or "missing" in msg
        assert "agent_subagent" in result.message
        assert "agent_model" in result.message
        assert "cross_skill_ref" in result.message
