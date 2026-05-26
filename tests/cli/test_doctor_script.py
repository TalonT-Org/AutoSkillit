"""Tests for the _check_script_binary doctor check."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from autoskillit.cli.doctor._doctor_runtime import _check_script_binary
from autoskillit.core import Severity

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


class TestCheckScriptBinary:
    """Doctor check for script(1) PTY binary availability."""

    def test_passes_when_script_available_and_supports_qefc(self) -> None:
        """_check_script_binary returns OK when script is available and -qefc works."""
        with patch(
            "subprocess.run",
            return_value=type("R", (), {"returncode": 0})(),
        ):
            result = _check_script_binary()

        assert result.severity == Severity.OK
        assert result.check == "script_binary"

    def test_fails_when_script_unavailable(self) -> None:
        """_check_script_binary returns WARNING when script(1) is not installed."""
        with patch("subprocess.run", side_effect=FileNotFoundError("script")):
            result = _check_script_binary()

        assert result.severity == Severity.WARNING
        assert result.check == "script_binary"
        assert "not found" in result.message.lower() or "unavailable" in result.message.lower()

    def test_fails_when_qefc_flags_unsupported(self) -> None:
        """_check_script_binary returns WARNING when -qefc flags fail."""
        with patch(
            "subprocess.run",
            return_value=type("R", (), {"returncode": 1})(),
        ):
            result = _check_script_binary()

        assert result.severity == Severity.WARNING
        assert result.check == "script_binary"
        assert "-qefc" in result.message or "unsupported" in result.message.lower()
