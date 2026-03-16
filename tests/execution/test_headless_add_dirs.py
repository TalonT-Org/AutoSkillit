"""Tests for run_headless_core multi-path --add-dir support (T-OVR-012..014)."""

from __future__ import annotations

import pytest

from autoskillit.core import ValidatedAddDir


@pytest.fixture
def make_ctx(tmp_path):
    from autoskillit.config import AutomationConfig
    from autoskillit.server._factory import make_context
    from tests.conftest import MockSubprocessRunner

    def factory(runner=None):
        ctx = make_context(
            AutomationConfig(),
            runner=runner or MockSubprocessRunner(),
            plugin_dir=str(tmp_path),
        )
        ctx.config.linux_tracing.log_dir = str(tmp_path / "session_logs")
        return ctx

    return factory


@pytest.mark.anyio
async def test_run_headless_core_no_add_dir_when_empty(make_ctx):
    """T-OVR-012: run_headless_core with empty add_dirs emits no --add-dir flags."""
    from autoskillit.execution.headless import run_headless_core
    from tests.conftest import _make_result

    captured_cmd = []

    async def mock_runner(cmd, **kwargs):
        captured_cmd.extend(cmd)
        return _make_result()

    ctx = make_ctx(runner=mock_runner)
    await run_headless_core("/autoskillit:investigate foo", "/tmp/proj", ctx, add_dirs=())
    assert "--add-dir" not in captured_cmd


@pytest.mark.anyio
async def test_run_headless_core_two_add_dirs(make_ctx, tmp_path):
    """T-OVR-013: run_headless_core with two ValidatedAddDir paths emits two --add-dir flags."""
    from autoskillit.execution.headless import run_headless_core
    from tests.conftest import _make_result

    # Create two valid add-dir layouts
    for name in ("a", "b"):
        skill_dir = tmp_path / name / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Test")

    dir_a = ValidatedAddDir(path=str(tmp_path / "a"))
    dir_b = ValidatedAddDir(path=str(tmp_path / "b"))

    captured_cmd = []

    async def mock_runner(cmd, **kwargs):
        captured_cmd.extend(cmd)
        return _make_result()

    ctx = make_ctx(runner=mock_runner)
    await run_headless_core(
        "/autoskillit:investigate foo",
        "/tmp/proj",
        ctx,
        add_dirs=[dir_a, dir_b],
    )
    add_dir_positions = [i for i, x in enumerate(captured_cmd) if x == "--add-dir"]
    assert len(add_dir_positions) == 2
    dirs_passed = [captured_cmd[i + 1] for i in add_dir_positions]
    assert str(tmp_path / "a") in dirs_passed
    assert str(tmp_path / "b") in dirs_passed


@pytest.mark.anyio
async def test_run_skill_passes_ephemeral_session_dir_as_add_dir(tool_ctx, monkeypatch):
    """T-OVR-014: run_skill passes ephemeral session dir (not raw skills_extended/) as add_dirs."""
    from autoskillit.core import SkillResult
    from autoskillit.server import _state

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
    monkeypatch.setattr(_state, "_ctx", tool_ctx)

    from autoskillit.server.tools_execution import run_skill

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
