"""Contract tests: backend prompt output must match declared skill_sigil."""

from __future__ import annotations

import pytest

from autoskillit.core import SkillSessionConfig, ValidatedAddDir
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.backends.codex import CodexBackend
from tests.fixtures.codex import prompt_text

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def _codex_skill_add_dirs(cwd: str) -> tuple[ValidatedAddDir, ...]:
    """A single ValidatedAddDir satisfying CodexBackend's app-server skill-session invariant."""
    return (
        ValidatedAddDir(
            path=f"{cwd}/add-dir",
            session_home=cwd,
            skill_entries=(("test-skill", "test-skill/SKILL.md"),),
        ),
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)


@pytest.mark.parametrize("backend", [ClaudeCodeBackend(), CodexBackend()])
def test_skill_prompt_uses_backend_sigil(backend):
    """Prompt produced by build_skill_session_cmd must contain the backend's declared sigil."""
    add_dirs = _codex_skill_add_dirs("/tmp") if isinstance(backend, CodexBackend) else ()
    spec = backend.build_skill_session_cmd("/test-skill arg", "/tmp", add_dirs=add_dirs)
    prompt = prompt_text(spec)
    sigil = backend.capabilities.skill_sigil
    assert f"{sigil}test-skill" in prompt or f"{sigil}autoskillit:test-skill" in prompt


@pytest.mark.parametrize("backend", [ClaudeCodeBackend(), CodexBackend()])
def test_skill_prompt_does_not_contain_wrong_sigil(backend):
    """Prompt must not contain another backend's sigil prefix for the skill name."""
    add_dirs = _codex_skill_add_dirs("/tmp") if isinstance(backend, CodexBackend) else ()
    spec = backend.build_skill_session_cmd("/test-skill arg", "/tmp", add_dirs=add_dirs)
    prompt = prompt_text(spec)
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
    config_kwargs: dict[str, object] = {"profile_name": "test-profile"}
    if isinstance(backend, CodexBackend):
        config_kwargs["add_dirs"] = _codex_skill_add_dirs("/tmp")
    spec = backend.build_skill_session_cmd(
        skill_cmd, "/tmp", config=SkillSessionConfig(**config_kwargs)
    )
    prompt = prompt_text(spec)
    if expect_preamble_reference:
        assert "After loading the skill instructions" in prompt
    else:
        assert "After loading the skill instructions" not in prompt
