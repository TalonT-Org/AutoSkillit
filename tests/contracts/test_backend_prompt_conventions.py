"""Contract tests: backend prompt output must match declared skill_sigil."""

from __future__ import annotations

import pytest

from autoskillit.core import CmdSpec, SkillSessionConfig
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.backends.codex import CodexBackend


def _extract_prompt(spec: CmdSpec) -> str:
    """Extract the prompt string from a CmdSpec, dispatching by backend command shape."""
    cmd = list(spec.cmd)
    if "-p" in cmd:
        return cmd[cmd.index("-p") + 1]
    return cmd[-1]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)


@pytest.mark.parametrize("backend", [ClaudeCodeBackend(), CodexBackend()])
def test_skill_prompt_uses_backend_sigil(backend):
    """Prompt produced by build_skill_session_cmd must contain the backend's declared sigil."""
    spec = backend.build_skill_session_cmd("/test-skill arg", "/tmp")
    prompt = _extract_prompt(spec)
    sigil = backend.capabilities.skill_sigil
    assert f"{sigil}test-skill" in prompt or f"{sigil}autoskillit:test-skill" in prompt


@pytest.mark.parametrize("backend", [ClaudeCodeBackend(), CodexBackend()])
def test_skill_prompt_does_not_contain_wrong_sigil(backend):
    """Prompt must not contain another backend's sigil prefix for the skill name."""
    spec = backend.build_skill_session_cmd("/test-skill arg", "/tmp")
    prompt = _extract_prompt(spec)
    wrong_sigils = {"/", "$"} - {backend.capabilities.skill_sigil}
    for wrong in wrong_sigils:
        assert f"Use the {wrong}test-skill skill" not in prompt


@pytest.mark.parametrize(
    "backend,skill_cmd,expect_preamble_reference",
    [
        (ClaudeCodeBackend(), "/test-skill", True),
        (CodexBackend(), "/test-skill", False),
    ],
)
def test_narration_suppression_matches_preamble(backend, skill_cmd, expect_preamble_reference):
    """Narration suppression must reference 'loading skill instructions' only with preamble."""
    spec = backend.build_skill_session_cmd(
        skill_cmd, "/tmp", config=SkillSessionConfig(profile_name="test-profile")
    )
    prompt = _extract_prompt(spec)
    if expect_preamble_reference:
        assert "After loading the skill instructions" in prompt
    else:
        assert "After loading the skill instructions" not in prompt
