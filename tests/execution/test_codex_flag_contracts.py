from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import FreshLaunch, OutputFormat, RestoreSession, ResumeWithBriefing
from autoskillit.core.types import CmdSpec
from autoskillit.execution.backends.codex import CodexBackend, CodexFlags
from tests.execution.backends._plugin_binding import plugin_binding
from tests.fixtures.codex import codex_skill_add_dirs

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_GENERATED_HOME = Path("/work/codex-home")
_CODEX_VALUE_BEARING_FLAGS = CodexBackend().interactive_ordering_flags()[1]


def _extract_flags(cmd: tuple[str, ...] | list[str]) -> set[str]:
    return {tok for tok in cmd if tok.startswith("-")}


SKILL_BASE: dict[str, object] = {
    "skill_command": "/test-skill",
    "cwd": "/work",
    "completion_marker": "%%DONE%%",
    "model": None,
    "plugin_binding": None,
    "output_format": OutputFormat.JSON,
    "add_dirs": codex_skill_add_dirs("/work"),
}


def _build_food_truck(*, resume_session_id: str = "") -> CmdSpec:
    with plugin_binding(Path("/pkg")) as binding:
        return CodexBackend().build_food_truck_cmd(
            orchestrator_prompt="dispatch the work",
            plugin_binding=binding,
            cwd="/work",
            completion_marker="%%DONE%%",
            resume_session_id=resume_session_id,
            managed_skill_catalog=codex_skill_add_dirs("/work")[0],
        )


class TestCodexExecFlagValues:
    def test_json_value(self) -> None:
        assert CodexFlags.JSON == "--json"

    def test_sandbox_value(self) -> None:
        assert CodexFlags.SANDBOX == "--sandbox"

    def test_model_value(self) -> None:
        assert CodexFlags.MODEL == "--model"

    def test_model_short_value(self) -> None:
        assert CodexFlags.MODEL_SHORT == "-m"

    def test_add_dir_value(self) -> None:
        assert CodexFlags.ADD_DIR == "--add-dir"

    def test_resume_subcommand_value(self) -> None:
        assert CodexFlags.RESUME_SUBCOMMAND == "resume"

    def test_config_override_value(self) -> None:
        assert CodexFlags.CONFIG_OVERRIDE == "-c"

    def test_dangerously_bypass_value(self) -> None:
        assert CodexFlags.DANGEROUSLY_BYPASS == "--dangerously-bypass-approvals-and-sandbox"

    def test_dangerously_bypass_hook_trust_value(self) -> None:
        assert CodexFlags.DANGEROUSLY_BYPASS_HOOK_TRUST == "--dangerously-bypass-hook-trust"


class TestCodexValueBearingFlagsSubset:
    def test_value_bearing_is_subset_of_codex_flags(self) -> None:
        all_flags = frozenset(CodexFlags)
        invalid = _CODEX_VALUE_BEARING_FLAGS - all_flags
        assert not invalid, (
            f"_CODEX_VALUE_BEARING_FLAGS contains entries not in CodexFlags: {invalid}"
        )


class TestNoApprovalFlagInExecBuilders:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)

    @pytest.mark.parametrize(
        "builder_call",
        [
            lambda: CodexBackend().build_skill_session_cmd(**SKILL_BASE),
            lambda: CodexBackend().build_skill_session_cmd(
                **{**SKILL_BASE, "resume_session_id": "sess-test"},
            ),
            _build_food_truck,
            lambda: _build_food_truck(resume_session_id="sess-test"),
            lambda: CodexBackend().build_headless_cmd("do stuff", generated_home=_GENERATED_HOME),
            lambda: CodexBackend().build_resume_cmd(
                resume_session_id="sess-test",
                prompt="continue",
                session_home=str(_GENERATED_HOME),
            ),
        ],
        ids=[
            "skill_session",
            "skill_session_resume",
            "food_truck",
            "food_truck_resume",
            "headless",
            "resume",
        ],
    )
    def test_no_approval_flag_in_exec_builders(self, builder_call) -> None:
        spec = builder_call()
        assert "-a" not in spec.cmd
        assert "--ask-for-approval" not in spec.cmd

    def test_no_approval_flag_in_value_bearing_flags(self) -> None:
        assert "-a" not in _CODEX_VALUE_BEARING_FLAGS
        assert "--ask-for-approval" not in _CODEX_VALUE_BEARING_FLAGS


class TestInteractiveCmdUsesNoExecOnlyFlags:
    @pytest.mark.parametrize(
        "launch",
        [
            FreshLaunch(),
            RestoreSession(session_id="sess-test"),
            ResumeWithBriefing(session_id="sess-test", briefing="continue"),
        ],
        ids=["fresh", "restore", "resume_with_briefing"],
    )
    def test_interactive_excludes_exec_only_flags(self, launch) -> None:
        spec = CodexBackend().build_interactive_cmd(launch=launch, generated_home=_GENERATED_HOME)
        flags = _extract_flags(spec.cmd)
        assert "--json" not in flags
        assert "--sandbox" not in flags
