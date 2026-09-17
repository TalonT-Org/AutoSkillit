"""Cross-backend structural coverage for interactive launch intent purity."""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest

from autoskillit.core import (
    FreshLaunch,
    InteractiveLaunch,
    PositionalRole,
    RestoreSession,
    ResumeWithBriefing,
)
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.backends.codex import CodexBackend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _build(backend: object, launch: InteractiveLaunch, tmp_path: Path):
    kwargs: dict[str, object] = {"launch": launch}
    if isinstance(backend, CodexBackend):
        kwargs["generated_home"] = tmp_path / "codex-home"
    return backend.build_interactive_cmd(**kwargs)  # type: ignore[attr-defined]


@pytest.mark.parametrize("backend", [ClaudeCodeBackend(), CodexBackend()])
@pytest.mark.parametrize(
    ("launch", "expected_prompts", "is_resume"),
    [
        (FreshLaunch(system_prompt="SYSTEM-SENTINEL", initial_prompt="FRESH"), ["FRESH"], False),
        (RestoreSession(session_id="session-1"), [], True),
        (
            ResumeWithBriefing(session_id="session-1", briefing="BRIEFING-SENTINEL"),
            ["BRIEFING-SENTINEL"],
            True,
        ),
    ],
)
def test_launch_intent_emits_only_authorized_prompt_positionals(
    backend: object,
    launch: InteractiveLaunch,
    expected_prompts: list[str],
    is_resume: bool,
    tmp_path: Path,
) -> None:
    spec = _build(backend, launch, tmp_path)

    assert spec.is_resume is is_resume
    assert spec.origin is not None
    assert [
        value for role, value in spec.origin.positional if role is PositionalRole.PROMPT
    ] == expected_prompts
    if isinstance(launch, FreshLaunch):
        assert any("SYSTEM-SENTINEL" in token for token in spec.cmd)
    else:
        assert all("SYSTEM-SENTINEL" not in token for token in spec.cmd)


def test_restore_session_has_no_prompt_constructor_field() -> None:
    with pytest.raises(TypeError):
        RestoreSession(session_id="session-1", initial_prompt="hello")  # type: ignore[call-arg]


@pytest.mark.parametrize("field", ("initial_prompt", "system_prompt"))
def test_resume_with_briefing_has_no_prompt_constructor_fields(field: str) -> None:
    with pytest.raises(TypeError):
        ResumeWithBriefing(
            session_id="session-1",
            briefing="continue",
            **{field: "hello"},  # type: ignore[arg-type]
        )


def test_resume_briefing_must_not_be_empty() -> None:
    with pytest.raises(ValueError, match="briefing must not be empty"):
        ResumeWithBriefing(session_id="session-1", briefing="")


def test_every_launch_variant_is_covered_by_this_module() -> None:
    covered = {FreshLaunch, RestoreSession, ResumeWithBriefing}
    assert set(get_args(InteractiveLaunch)) == covered
