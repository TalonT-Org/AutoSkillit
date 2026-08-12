"""T5: Order CLI detects infrastructure exit and auto-resumes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from autoskillit.cli.session._session_launch import (
    _InfraExitSignal,
    _launch_cook_session,
)
from autoskillit.core import NamedResume
from autoskillit.execution.backends.claude import ClaudeCodeBackend

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _launch_kwargs() -> dict[str, object]:
    return {
        "backend": ClaudeCodeBackend(),
        "skill_compilation": SimpleNamespace(unavailable=(), catalog=None),
        "launch_id": "test-order",
        "default_base_branch": "main",
        "workspace_temp_dir": None,
    }


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
            _launch_cook_session("prompt", required_env=frozenset(), **_launch_kwargs())

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
            _launch_cook_session("prompt", required_env=frozenset(), **_launch_kwargs())

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
            _launch_cook_session("prompt", required_env=frozenset(), **_launch_kwargs())

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
            _launch_cook_session("prompt", required_env=frozenset(), **_launch_kwargs())

        assert call_count == 1
