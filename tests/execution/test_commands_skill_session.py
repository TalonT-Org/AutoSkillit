"""Cross-backend contract tests for build_skill_session_cmd shared invariants."""

from __future__ import annotations

import pytest

from autoskillit.execution.backends import CodexBackend
from autoskillit.execution.backends.claude import ClaudeCodeBackend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)


def _prompt_text(spec) -> str:
    """Extract the prompt token from a CmdSpec in a backend-agnostic way."""
    cmd = list(spec.cmd)
    if "-p" in cmd:
        return cmd[cmd.index("-p") + 1]
    return cmd[-1]


class TestBuildSkillSessionCmdSharedBehavior:
    @pytest.mark.parametrize("backend", [ClaudeCodeBackend(), CodexBackend()])
    def test_completion_directive_injected(self, backend) -> None:
        spec = backend.build_skill_session_cmd(
            "/autoskillit:investigate", "/repo", completion_marker="DONE"
        )
        assert "DONE" in _prompt_text(spec)

    @pytest.mark.parametrize("backend", [ClaudeCodeBackend(), CodexBackend()])
    def test_cwd_anchor_injected(self, backend) -> None:
        spec = backend.build_skill_session_cmd(
            "/autoskillit:investigate", "/repo", completion_marker="DONE"
        )
        assert "/repo" in _prompt_text(spec)

    @pytest.mark.parametrize("backend", [ClaudeCodeBackend(), CodexBackend()])
    def test_headless_env_set(self, backend) -> None:
        spec = backend.build_skill_session_cmd(
            "/autoskillit:investigate", "/repo", completion_marker="DONE"
        )
        assert spec.env["AUTOSKILLIT_HEADLESS"] == "1"

    @pytest.mark.parametrize("backend", [ClaudeCodeBackend(), CodexBackend()])
    def test_session_type_env_set(self, backend) -> None:
        spec = backend.build_skill_session_cmd(
            "/autoskillit:investigate", "/repo", completion_marker="DONE"
        )
        assert spec.env["AUTOSKILLIT_SESSION_TYPE"] == "skill"

    @pytest.mark.parametrize("backend", [ClaudeCodeBackend(), CodexBackend()])
    def test_skill_name_extracted(self, backend) -> None:
        spec = backend.build_skill_session_cmd(
            "/autoskillit:investigate", "/repo", completion_marker="DONE"
        )
        assert spec.env["AUTOSKILLIT_SKILL_NAME"] == "investigate"

    @pytest.mark.parametrize("backend", [ClaudeCodeBackend(), CodexBackend()])
    def test_agent_backend_present_in_env(self, backend) -> None:
        spec = backend.build_skill_session_cmd(
            "/autoskillit:investigate", "/repo", completion_marker="DONE"
        )
        assert "AUTOSKILLIT_AGENT_BACKEND" in spec.env

    @pytest.mark.parametrize(
        ("backend", "expected"),
        [
            (ClaudeCodeBackend(), "skill_load_guard"),
            (CodexBackend(), ""),
        ],
    )
    def test_applicable_guards_env_set(self, backend, expected) -> None:
        spec = backend.build_skill_session_cmd(
            "/autoskillit:investigate", "/repo", completion_marker="DONE"
        )
        assert spec.env["AUTOSKILLIT_APPLICABLE_GUARDS"] == expected
