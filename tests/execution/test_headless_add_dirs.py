"""Tests for run_headless_core multi-path --add-dir support (T-OVR-012..013)."""

from __future__ import annotations

import pytest

from autoskillit.core import ValidatedAddDir
from autoskillit.execution.backends import ClaudeCodeBackend, CodexBackend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


@pytest.mark.anyio
async def test_run_headless_core_no_add_dir_when_empty(minimal_ctx, tmp_path):
    """T-OVR-012: run_headless_core with empty add_dirs emits no --add-dir flags."""
    from autoskillit.execution.headless import run_headless_core
    from tests.conftest import _make_result

    captured_cmd = []

    async def mock_runner(cmd, **kwargs):
        captured_cmd.extend(cmd)
        return _make_result()

    minimal_ctx.runner = mock_runner
    minimal_ctx.backend = ClaudeCodeBackend()
    proj = tmp_path / "proj"
    proj.mkdir()
    await run_headless_core("/autoskillit:investigate foo", str(proj), minimal_ctx, add_dirs=())
    assert "--add-dir" not in captured_cmd


@pytest.mark.anyio
async def test_run_headless_core_two_add_dirs(minimal_ctx, tmp_path):
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

    minimal_ctx.runner = mock_runner
    minimal_ctx.backend = ClaudeCodeBackend()
    proj = tmp_path / "proj"
    proj.mkdir()
    await run_headless_core(
        "/autoskillit:investigate foo",
        str(proj),
        minimal_ctx,
        add_dirs=[dir_a, dir_b],
    )
    add_dir_positions = [i for i, x in enumerate(captured_cmd) if x == "--add-dir"]
    assert len(add_dir_positions) == 2
    dirs_passed = [captured_cmd[i + 1] for i in add_dir_positions]
    assert str(tmp_path / "a") in dirs_passed
    assert str(tmp_path / "b") in dirs_passed


@pytest.mark.anyio
async def test_codex_add_dir_uses_generated_home_without_artifact_binding(
    minimal_ctx,
    tmp_path,
) -> None:
    from autoskillit.execution.headless import run_headless_core
    from tests.conftest import _make_result

    class NoArtifactAuthority:
        acquired = False

        def acquire_launch_binding(self, *, backend, load_mode):
            self.acquired = True
            pytest.fail(f"Codex add-dir must not acquire {load_mode.value} artifact authority")

    generated_home = tmp_path / "codex-home"
    generated_home.mkdir()
    authority = NoArtifactAuthority()
    captured_kwargs = {}

    async def mock_runner(_cmd, **kwargs):
        captured_kwargs.update(kwargs)
        return _make_result()

    minimal_ctx.runner = mock_runner
    minimal_ctx.backend = CodexBackend()
    minimal_ctx.plugin_authority = authority
    await run_headless_core(
        "/autoskillit:investigate foo",
        str(tmp_path),
        minimal_ctx,
        add_dirs=[ValidatedAddDir(path=str(generated_home))],
    )

    assert authority.acquired is False
    assert captured_kwargs["pass_fds"] == ()
    assert captured_kwargs["env"]["CODEX_HOME"] == str(generated_home)
