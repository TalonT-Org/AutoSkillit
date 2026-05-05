"""T4: Resume prompt injection includes completed work."""

from __future__ import annotations

import pytest

from autoskillit.core.types._type_checkpoint import SessionCheckpoint
from autoskillit.execution.commands import _build_resume_context

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestBuildResumeContext:
    def test_lists_completed_items(self) -> None:
        cp = SessionCheckpoint(
            completed_items=["issue/1", "issue/2"],
            step_name="fleet_dispatch",
        )
        ctx = _build_resume_context(cp)
        assert "COMPLETED: issue/1" in ctx
        assert "COMPLETED: issue/2" in ctx

    def test_includes_step_name(self) -> None:
        cp = SessionCheckpoint(completed_items=["x"], step_name="my_step")
        ctx = _build_resume_context(cp)
        assert "Last active step: my_step" in ctx

    def test_skip_instruction(self) -> None:
        cp = SessionCheckpoint(completed_items=["a"])
        ctx = _build_resume_context(cp)
        assert "MUST be skipped" in ctx

    def test_no_step_name_omits_line(self) -> None:
        cp = SessionCheckpoint(completed_items=["a"], step_name="")
        ctx = _build_resume_context(cp)
        assert "Last active step" not in ctx


class TestBuildSkillSessionCmdResume:
    BASE = dict(
        skill_command="Use /test-skill",
        cwd="/tmp/test",
        completion_marker="%%DONE%%",
        model=None,
        plugin_source=None,
        output_format="text",
        resume_session_id="sess-123",
    )

    def test_resume_without_checkpoint(self) -> None:
        from autoskillit.execution.commands import build_skill_session_cmd

        spec = build_skill_session_cmd(**self.BASE)
        prompt = spec.cmd[spec.cmd.index("--prompt") + 1] if "--prompt" in spec.cmd else ""
        assert "interrupted before completion" in prompt
        assert "RESUME CONTEXT" not in prompt

    def test_resume_with_checkpoint(self) -> None:
        from autoskillit.execution.commands import build_skill_session_cmd

        cp = SessionCheckpoint(completed_items=["done_a", "done_b"], step_name="step_x")
        spec = build_skill_session_cmd(**self.BASE, resume_checkpoint=cp)
        prompt = spec.cmd[spec.cmd.index("--prompt") + 1] if "--prompt" in spec.cmd else ""
        assert "RESUME CONTEXT" in prompt
        assert "COMPLETED: done_a" in prompt
        assert "COMPLETED: done_b" in prompt

    def test_resume_with_empty_checkpoint_no_context(self) -> None:
        from autoskillit.execution.commands import build_skill_session_cmd

        cp = SessionCheckpoint(completed_items=[], step_name="")
        spec = build_skill_session_cmd(**self.BASE, resume_checkpoint=cp)
        prompt = spec.cmd[spec.cmd.index("--prompt") + 1] if "--prompt" in spec.cmd else ""
        assert "RESUME CONTEXT" not in prompt
