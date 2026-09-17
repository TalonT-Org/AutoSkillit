"""Behavioral coverage for the resumed interactive prompt-purity gate."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    CmdOrigin,
    CmdSpec,
    FreshLaunch,
    PositionalRole,
    RestoreSession,
    ResumeWithBriefing,
)
from autoskillit.execution import assert_resume_purity

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _spec(
    *,
    is_resume: bool,
    positional: tuple[tuple[PositionalRole, str], ...] = (),
    with_origin: bool = True,
) -> CmdSpec:
    origin = CmdOrigin(binary="agent", positional=positional) if with_origin else None
    return CmdSpec(
        cmd=("agent", *(value for _role, value in positional)),
        env={},
        origin=origin,
        is_resume=is_resume,
    )


def test_restore_rejects_prompt_role() -> None:
    with pytest.raises(ValueError, match="cannot carry a prompt"):
        assert_resume_purity(
            spec=_spec(
                is_resume=True,
                positional=((PositionalRole.PROMPT, "unsolicited"),),
            ),
            launch=RestoreSession(session_id="session-1"),
        )


def test_restore_accepts_only_resume_target_role() -> None:
    assert_resume_purity(
        spec=_spec(
            is_resume=True,
            positional=((PositionalRole.RESUME_TARGET, "session-1"),),
        ),
        launch=RestoreSession(session_id="session-1"),
    )


def test_fresh_accepts_its_prompt() -> None:
    assert_resume_purity(
        spec=_spec(
            is_resume=False,
            positional=((PositionalRole.PROMPT, "hello"),),
        ),
        launch=FreshLaunch(initial_prompt="hello"),
    )


def test_resumed_intent_requires_origin() -> None:
    with pytest.raises(ValueError, match="requires CmdOrigin"):
        assert_resume_purity(
            spec=_spec(is_resume=True, with_origin=False),
            launch=RestoreSession(session_id="session-1"),
        )


def test_resume_flag_must_agree_with_launch_intent() -> None:
    with pytest.raises(ValueError, match="disagrees"):
        assert_resume_purity(
            spec=_spec(is_resume=False),
            launch=RestoreSession(session_id="session-1"),
        )


@pytest.mark.parametrize(
    "positional",
    [
        ((PositionalRole.PROMPT, "different"),),
        (
            (PositionalRole.PROMPT, "briefing"),
            (PositionalRole.PROMPT, "extra"),
        ),
    ],
)
def test_briefing_rejects_different_or_extra_prompt(
    positional: tuple[tuple[PositionalRole, str], ...],
) -> None:
    with pytest.raises(ValueError, match="exactly its declared"):
        assert_resume_purity(
            spec=_spec(is_resume=True, positional=positional),
            launch=ResumeWithBriefing(session_id="session-1", briefing="briefing"),
        )


def test_briefing_accepts_exact_declared_prompt() -> None:
    assert_resume_purity(
        spec=_spec(
            is_resume=True,
            positional=(
                (PositionalRole.RESUME_TARGET, "session-1"),
                (PositionalRole.PROMPT, "briefing"),
            ),
        ),
        launch=ResumeWithBriefing(session_id="session-1", briefing="briefing"),
    )
