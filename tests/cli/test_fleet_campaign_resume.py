"""Fleet launch intent must map to each interactive backend's real argv."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


@pytest.mark.parametrize("backend_name", ("claude", "codex"))
@pytest.mark.parametrize(
    "launch",
    (
        pytest.param(
            "fresh",
            id="fresh",
        ),
        pytest.param(
            "briefed-resume",
            id="briefed-resume",
        ),
        pytest.param(
            "restore",
            id="restore",
        ),
    ),
)
def test_fleet_launch_intents_build_expected_interactive_argv(
    backend_name: str,
    launch: str,
    tmp_path: Path,
) -> None:
    """Both backends preserve fresh prompts and only submit an explicit briefing."""
    from autoskillit.core import (
        FreshLaunch,
        RestoreSession,
        ResumeWithBriefing,
    )
    from autoskillit.execution.backends.claude import ClaudeCodeBackend
    from autoskillit.execution.backends.codex import CodexBackend

    fresh_system_prompt = "fleet fresh system prompt"
    fresh_greeting = "fleet fresh greeting"
    session_id = "fleet-prior-session"
    briefing = "Resume the fleet at dispatch dispatch-1."
    if launch == "fresh":
        intent = FreshLaunch(
            system_prompt=fresh_system_prompt,
            initial_prompt=fresh_greeting,
        )
    elif launch == "briefed-resume":
        intent = ResumeWithBriefing(session_id=session_id, briefing=briefing)
    else:
        intent = RestoreSession(session_id=session_id)

    if backend_name == "claude":
        spec = ClaudeCodeBackend().build_interactive_cmd(
            launch=intent,
            project_root=tmp_path,
        )
    else:
        spec = CodexBackend(
            source_codex_home=tmp_path / "source-codex-home"
        ).build_interactive_cmd(
            launch=intent,
            generated_home=tmp_path / "generated-codex-home",
        )

    argv = "\n".join(spec.cmd)
    if isinstance(intent, FreshLaunch):
        assert not spec.is_resume
        assert fresh_system_prompt in argv
        assert fresh_greeting in spec.cmd
    elif isinstance(intent, ResumeWithBriefing):
        assert spec.is_resume
        assert session_id in spec.cmd
        assert briefing in spec.cmd
        assert fresh_system_prompt not in argv
        assert fresh_greeting not in argv
    else:
        assert isinstance(intent, RestoreSession)
        assert spec.is_resume
        assert session_id in spec.cmd
        assert briefing not in argv
        assert fresh_system_prompt not in argv
        assert fresh_greeting not in argv
