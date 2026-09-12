"""Contract tests: backend sandbox flag invariants."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import CmdSpec, OutputFormat, SkillSessionConfig
from autoskillit.execution.backends import ClaudeCodeBackend, CodexBackend
from tests.execution.backends._plugin_binding import plugin_binding
from tests.fixtures.codex import codex_skill_add_dirs

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

# Deliberately decoupled from the builder calls' cwd="" below — this fixture's
# session_home is independent of the launch cwd, so it stays hardcoded here
# rather than threading cwd through.
_SKILL_SESSION_ADD_DIRS = codex_skill_add_dirs("/work")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)


class TestCodexSandboxInvariants:
    @pytest.mark.parametrize(
        ("sandbox_mode", "expected_sandbox"),
        [("workspace-write", "workspace-write"), ("read-only", "read-only")],
    )
    def test_build_skill_session_cmd_sandbox_mode(
        self, sandbox_mode: str, expected_sandbox: str
    ) -> None:
        config = SkillSessionConfig(sandbox_mode=sandbox_mode, add_dirs=_SKILL_SESSION_ADD_DIRS)
        spec: CmdSpec = CodexBackend().build_skill_session_cmd(
            "/test-skill", cwd="", config=config
        )
        assert spec.app_server_plan is not None
        assert spec.app_server_plan.sandbox == expected_sandbox

    def test_build_food_truck_cmd_sandbox_read_only(self) -> None:
        spec: CmdSpec = CodexBackend().build_food_truck_cmd(
            orchestrator_prompt="dispatch",
            plugin_binding=plugin_binding(Path("/pkg")),
            cwd="",
            completion_marker="%%DONE%%",
        )
        positions = [i for i, v in enumerate(spec.cmd) if v == "--sandbox"]
        assert len(positions) == 1, f"expected exactly one --sandbox, got {len(positions)}"
        assert spec.cmd[positions[0] + 1] == "read-only"


class TestClaudeCodeSandboxAbsence:
    def test_build_skill_session_cmd_no_sandbox_flag(self) -> None:
        spec: CmdSpec = ClaudeCodeBackend().build_skill_session_cmd(
            "/test-skill",
            cwd="",
            completion_marker="%%DONE%%",
            model=None,
            plugin_binding=None,
            output_format=OutputFormat.JSON,
        )
        assert "--sandbox" not in spec.cmd


class TestNetworkAccessOverride:
    """T-A5, T-A6: network_access in SkillSessionConfig drives Codex extra override."""

    def test_skill_session_with_network_access_gets_override(self) -> None:
        config = SkillSessionConfig(network_access=True, add_dirs=_SKILL_SESSION_ADD_DIRS)
        spec: CmdSpec = CodexBackend().build_skill_session_cmd(
            "/test-skill", cwd="", config=config
        )
        assert spec.app_server_plan is not None
        overrides = spec.app_server_plan.config_overrides
        assert overrides.get("sandbox_workspace_write.network_access") is True, (
            f"Expected network_access override in plan, got: {overrides}"
        )

    def test_skill_session_without_network_access_no_override(self) -> None:
        config = SkillSessionConfig(add_dirs=_SKILL_SESSION_ADD_DIRS)
        spec: CmdSpec = CodexBackend().build_skill_session_cmd(
            "/test-skill", cwd="", config=config
        )
        assert spec.app_server_plan is not None
        overrides = spec.app_server_plan.config_overrides
        assert not overrides.get("sandbox_workspace_write.network_access"), (
            f"Unexpected network_access override in plan: {overrides}"
        )
