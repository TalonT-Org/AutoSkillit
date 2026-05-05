"""T5: Order CLI detects infrastructure exit and auto-resumes."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from autoskillit.cli.session._session_launch import (
    _InfraExitSignal,
    _launch_cook_session,
)
from autoskillit.core import NamedResume

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


class TestLaunchCookSessionInfraResume:
    def test_infra_exit_triggers_resume(self) -> None:
        call_count = 0

        def mock_run_interactive(system_prompt, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _InfraExitSignal(session_id="dead-sess", category="context_exhausted")
            return None

        with patch(
            "autoskillit.cli.session._session_launch._run_interactive_session",
            side_effect=mock_run_interactive,
        ):
            _launch_cook_session("prompt")

        assert call_count == 2

    def test_infra_exit_uses_named_resume(self) -> None:
        resume_specs: list = []

        def mock_run_interactive(system_prompt, **kwargs):
            resume_specs.append(kwargs.get("resume_spec"))
            if len(resume_specs) == 1:
                return _InfraExitSignal(session_id="sess-42", category="api_error")
            return None

        with patch(
            "autoskillit.cli.session._session_launch._run_interactive_session",
            side_effect=mock_run_interactive,
        ):
            _launch_cook_session("prompt")

        assert isinstance(resume_specs[1], NamedResume)
        assert resume_specs[1].session_id == "sess-42"

    def test_max_infra_resumes_exceeded(self) -> None:
        def mock_run_interactive(system_prompt, **kwargs):
            return _InfraExitSignal(session_id="sess-loop", category="process_killed")

        with (
            patch(
                "autoskillit.cli.session._session_launch._run_interactive_session",
                side_effect=mock_run_interactive,
            ),
            pytest.raises(SystemExit, match="Too many infrastructure resumes"),
        ):
            _launch_cook_session("prompt")

    def test_no_resume_on_clean_exit(self) -> None:
        call_count = 0

        def mock_run_interactive(system_prompt, **kwargs):
            nonlocal call_count
            call_count += 1
            return None

        with patch(
            "autoskillit.cli.session._session_launch._run_interactive_session",
            side_effect=mock_run_interactive,
        ):
            _launch_cook_session("prompt")

        assert call_count == 1
