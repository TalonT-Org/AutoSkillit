"""Contract tests: run_skill passes correct add_dirs to executor (T-OVR-014)."""

from __future__ import annotations

import pytest

from autoskillit.core import ValidatedAddDir


@pytest.mark.anyio
async def test_raw_skills_extended_excluded_from_run_skill_add_dirs(tool_ctx):
    """T-OVR-014: run_skill passes ephemeral session dir (not raw skills_extended/) as add_dirs."""
    from autoskillit.core import SkillResult
    from autoskillit.server.tools_execution import run_skill

    captured: dict = {}

    class MockExecutor:
        async def run(self, skill_command, cwd, *, add_dirs=(), **kwargs):
            captured["add_dirs"] = add_dirs
            captured["cwd"] = cwd
            return SkillResult(
                success=True,
                result="ok",
                session_id="",
                subtype="success",
                is_error=False,
                exit_code=0,
                needs_retry=False,
                retry_reason="none",
                stderr="",
                token_usage=None,
            )

    tool_ctx.executor = MockExecutor()

    await run_skill("/autoskillit:investigate foo", "/some/cwd")

    # All add_dirs entries must be ValidatedAddDir instances
    assert all(isinstance(d, ValidatedAddDir) for d in captured["add_dirs"])

    # At least one add_dir must have .claude/skills/*/SKILL.md layout
    from pathlib import Path

    has_skills = False
    for d in captured["add_dirs"]:
        p = Path(d.path)
        if list(p.glob(".claude/skills/*/SKILL.md")):
            has_skills = True
            break
    assert has_skills, "run_skill must pass an --add-dir with discoverable skills"

    # Must NOT include raw skills_extended/ path
    from autoskillit.workspace.skills import bundled_skills_extended_dir

    skills_ext = str(bundled_skills_extended_dir())
    add_dir_paths = [d.path for d in captured["add_dirs"]]
    assert skills_ext not in add_dir_paths, (
        "run_skill must not pass skills_extended/ directly — "
        "it should route through DefaultSessionSkillManager"
    )
