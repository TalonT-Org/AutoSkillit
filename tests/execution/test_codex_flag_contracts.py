from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import BareResume, DirectInstall, NamedResume, NoResume, OutputFormat
from autoskillit.execution.backends.codex import (
    CODEX_EXEC_FLAGS,
    CODEX_TOP_LEVEL_ONLY_FLAGS,
    CodexBackend,
    CodexFlags,
)
from autoskillit.execution.headless._headless_helpers import _CODEX_VALUE_BEARING_FLAGS

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _extract_flags(cmd: tuple[str, ...] | list[str]) -> set[str]:
    return {tok for tok in cmd if tok.startswith("-")}


SKILL_BASE: dict[str, object] = {
    "skill_command": "/test-skill",
    "cwd": "/work",
    "completion_marker": "%%DONE%%",
    "model": None,
    "plugin_source": None,
    "output_format": OutputFormat.JSON,
}

FOOD_TRUCK_BASE: dict[str, object] = {
    "orchestrator_prompt": "dispatch the work",
    "plugin_source": DirectInstall(plugin_dir=Path("/pkg")),
    "cwd": "/work",
    "completion_marker": "%%DONE%%",
}


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


class TestCodexFlagRegistryAudit:
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
            lambda: CodexBackend().build_food_truck_cmd(**FOOD_TRUCK_BASE),
            lambda: CodexBackend().build_headless_cmd("do stuff"),
            lambda: CodexBackend().build_resume_cmd(
                resume_session_id="sess-test", prompt="continue"
            ),
        ],
        ids=[
            "skill_session",
            "skill_session_resume",
            "food_truck",
            "headless",
            "resume",
        ],
    )
    def test_exec_builder_flags_are_all_in_codex_exec_flags(self, builder_call) -> None:
        spec = builder_call()
        flags = _extract_flags(spec.cmd)
        unknown = flags - CODEX_EXEC_FLAGS
        assert not unknown, (
            f"Builder produced flags not valid for codex exec: {unknown}. "
            f"If this flag is valid for codex exec, add it to CODEX_EXEC_FLAGS."
        )


class TestCodexExecFlagMetadataCoverage:
    def test_every_flag_member_is_categorized(self) -> None:
        flag_members = {m for m in CodexFlags if str(m).startswith("-")}
        categorized = CODEX_EXEC_FLAGS | CODEX_TOP_LEVEL_ONLY_FLAGS
        uncategorized = flag_members - categorized
        assert not uncategorized, (
            f"CodexFlags members not categorized in CODEX_EXEC_FLAGS or "
            f"CODEX_TOP_LEVEL_ONLY_FLAGS: {uncategorized}. "
            f"Add to the appropriate set."
        )

    def test_exec_and_top_level_are_disjoint(self) -> None:
        overlap = CODEX_EXEC_FLAGS & CODEX_TOP_LEVEL_ONLY_FLAGS
        assert not overlap, f"Flags in both sets: {overlap}"

    def test_all_categorized_flags_are_valid_members(self) -> None:
        all_flags = frozenset(CodexFlags)
        categorized = CODEX_EXEC_FLAGS | CODEX_TOP_LEVEL_ONLY_FLAGS
        invalid = categorized - all_flags
        assert not invalid, f"Categorized flags not in CodexFlags: {invalid}"


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
            lambda: CodexBackend().build_food_truck_cmd(**FOOD_TRUCK_BASE),
            lambda: CodexBackend().build_headless_cmd("do stuff"),
            lambda: CodexBackend().build_resume_cmd(
                resume_session_id="sess-test", prompt="continue"
            ),
        ],
        ids=[
            "skill_session",
            "skill_session_resume",
            "food_truck",
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
        "resume_spec",
        [NoResume(), NamedResume(session_id="sess-test"), BareResume()],
        ids=["no_resume", "named_resume", "bare_resume"],
    )
    def test_interactive_excludes_exec_only_flags(self, resume_spec) -> None:
        spec = CodexBackend().build_interactive_cmd(resume_spec=resume_spec)
        flags = _extract_flags(spec.cmd)
        assert "--json" not in flags
        assert "--sandbox" not in flags
