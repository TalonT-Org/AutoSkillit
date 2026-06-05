"""Contract tests: backend sandbox flag invariants."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import CmdSpec, DirectInstall, OutputFormat, SkillSessionConfig
from autoskillit.execution.backends import ClaudeCodeBackend, CodexBackend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)


class TestCodexSandboxInvariants:
    @pytest.mark.parametrize("sandbox_mode", ["workspace-write", "read-only"])
    def test_build_skill_session_cmd_sandbox_mode(self, sandbox_mode: str) -> None:
        config = SkillSessionConfig(sandbox_mode=sandbox_mode)
        spec: CmdSpec = CodexBackend().build_skill_session_cmd(
            "/test-skill", cwd="", config=config
        )
        assert spec.cmd[spec.cmd.index("--sandbox") + 1] == sandbox_mode

    def test_build_food_truck_cmd_sandbox_read_only(self) -> None:
        spec: CmdSpec = CodexBackend().build_food_truck_cmd(
            orchestrator_prompt="dispatch",
            plugin_source=DirectInstall(plugin_dir=Path("/pkg")),
            cwd="",
            completion_marker="%%DONE%%",
        )
        assert "--sandbox" in spec.cmd
        assert spec.cmd[spec.cmd.index("--sandbox") + 1] == "read-only"


class TestClaudeCodeSandboxAbsence:
    def test_build_skill_session_cmd_no_sandbox_flag(self) -> None:
        spec: CmdSpec = ClaudeCodeBackend().build_skill_session_cmd(
            "/test-skill",
            cwd="",
            completion_marker="%%DONE%%",
            model=None,
            plugin_source=None,
            output_format=OutputFormat.JSON,
        )
        assert "--sandbox" not in spec.cmd
