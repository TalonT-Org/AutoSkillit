"""Tests for CmdSpec, ClaudeEventData, CodexEventData, SessionEvent, AgentSessionResult."""

from __future__ import annotations

import dataclasses
import typing
from dataclasses import FrozenInstanceError

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_cmd_spec_frozen():
    from autoskillit.core import CmdSpec

    spec = CmdSpec(cmd=("claude", "--model", "opus"), env={"FOO": "bar"})
    with pytest.raises(FrozenInstanceError):
        spec.cmd = ()  # type: ignore[misc]


def test_cmd_spec_fields():
    from autoskillit.core import CmdSpec

    fields = {f.name for f in dataclasses.fields(CmdSpec)}
    assert fields == {
        "cmd",
        "env",
        "cwd",
        "origin",
        "is_resume",
        "process_idle_timeout_ms",
        "inherited_fds",
        "force_inactive_agent_teams",
    }


def test_cmd_spec_is_resume_default():
    from autoskillit.core import CmdSpec

    spec = CmdSpec(cmd=(), env={})
    assert spec.is_resume is False


def test_cmd_spec_process_idle_timeout_default():
    from autoskillit.core import CmdSpec

    spec = CmdSpec(cmd=(), env={})
    assert spec.process_idle_timeout_ms == 0


def test_cmd_spec_inherited_fds_default():
    from autoskillit.core import CmdSpec

    spec = CmdSpec(cmd=(), env={})
    assert spec.inherited_fds == ()


def test_cmd_spec_normalizes_inherited_fds():
    from autoskillit.core import CmdSpec

    spec = CmdSpec(cmd=(), env={}, inherited_fds=(7, 3, 7))

    assert spec.inherited_fds == (7, 3)


@pytest.mark.parametrize("invalid", [True, -1, 1.5, "3"])
def test_cmd_spec_rejects_invalid_inherited_fds(invalid: object):
    from autoskillit.core import CmdSpec

    with pytest.raises(ValueError, match="non-negative integers"):
        CmdSpec(cmd=(), env={}, inherited_fds=(invalid,))  # type: ignore[arg-type]


def test_cmd_spec_env_accepts_mapping():
    from collections.abc import Mapping

    from autoskillit.core import CmdSpec

    hints = typing.get_type_hints(CmdSpec)
    assert hints["env"] == Mapping[str, str]


def test_cmd_spec_slots():
    from autoskillit.core import CmdSpec

    assert hasattr(CmdSpec, "__slots__")


def test_claude_event_data_frozen():
    from autoskillit.core import ClaudeEventData

    ev = ClaudeEventData(record_type="assistant", subtype="text", session_id="s1")
    with pytest.raises(FrozenInstanceError):
        ev.record_type = "x"  # type: ignore[misc]


def test_claude_event_data_raw_default():
    from autoskillit.core import ClaudeEventData

    ev = ClaudeEventData(record_type="r", subtype="s", session_id="id")
    assert ev.raw == {}


def test_codex_event_data_frozen():
    from autoskillit.core import CodexEventData

    ev = CodexEventData(record_type="item", thread_id="t1", item_type="msg")
    with pytest.raises(FrozenInstanceError):
        ev.record_type = "x"  # type: ignore[misc]


def test_codex_event_data_raw_default():
    from autoskillit.core import CodexEventData

    ev = CodexEventData(record_type="r", thread_id="t", item_type="i")
    assert ev.raw == {}


def test_codex_event_data_fields_exhaustive():
    import dataclasses

    from autoskillit.core import CodexEventData

    fields = {f.name for f in dataclasses.fields(CodexEventData)}
    assert fields == {
        "record_type",
        "thread_id",
        "item_type",
        "raw",
        "usage",
        "file_changes",
        "command",
    }


def test_codex_event_data_new_fields_default_none():
    from autoskillit.core import CodexEventData

    ev = CodexEventData(record_type="item", thread_id="t1", item_type="msg")
    assert ev.usage is None
    assert ev.file_changes is None
    assert ev.command is None


def test_codex_event_data_new_fields_accept_values():
    from autoskillit.core import CodexEventData

    ev = CodexEventData(
        record_type="item",
        thread_id="t1",
        item_type="msg",
        raw={},
        usage={"input_tokens": 100, "output_tokens": 50},
        file_changes=({"path": "foo.py", "action": "edit"},),
        command="python foo.py",
    )
    assert ev.usage == {"input_tokens": 100, "output_tokens": 50}
    assert ev.file_changes == ({"path": "foo.py", "action": "edit"},)
    assert ev.command == "python foo.py"


def test_codex_event_data_field_types():
    import typing
    from collections.abc import Mapping

    from autoskillit.core import CodexEventData

    hints = typing.get_type_hints(CodexEventData)
    assert hints["usage"] == Mapping[str, typing.Any] | None
    assert hints["file_changes"] == tuple[Mapping[str, typing.Any], ...] | None
    assert hints["command"] == str | None


def test_session_event_frozen():
    from autoskillit.core import BackendEventKind, SessionEvent

    ev = SessionEvent(kind=BackendEventKind.COMPLETION, is_terminal=False, has_marker=False)
    with pytest.raises(FrozenInstanceError):
        ev.kind = BackendEventKind.ERROR  # type: ignore[misc]


def test_session_event_backend_data_union_type():
    from autoskillit.core import ClaudeEventData, CodexEventData, SessionEvent

    hints = typing.get_type_hints(SessionEvent)
    assert hints["backend_data"] == ClaudeEventData | CodexEventData | None


def test_session_event_defaults():
    from autoskillit.core import BackendEventKind, SessionEvent

    ev = SessionEvent(kind=BackendEventKind.ERROR, is_terminal=True, has_marker=False)
    assert ev.session_id is None
    assert ev.exit_code is None
    assert ev.backend_data is None


def test_agent_session_result_frozen():
    from autoskillit.core import AgentSessionResult

    r = AgentSessionResult(
        success=True, exit_code=0, session_id="s1", backend_name="claude-code", elapsed_seconds=5.0
    )
    with pytest.raises(FrozenInstanceError):
        r.success = False  # type: ignore[misc]


def test_agent_session_result_raw_default():
    from autoskillit.core import AgentSessionResult

    r = AgentSessionResult(
        success=True, exit_code=0, session_id="s", backend_name="b", elapsed_seconds=1.0
    )
    assert r.output == ""
    assert r.error == ""
    assert r.raw == {}


def test_session_summary_frozen_slots_and_exact_fields():
    from autoskillit.core import SessionSummary

    summary = SessionSummary(
        backend_name="codex",
        session_id="thread-1",
        launch_id="launch-1",
        cwd="/tmp/project",
        first_prompt="Fix the race",
        summary="Codex history isolation",
        git_branch="feature/history",
        modified="2026-07-23T10:00:00Z",
        is_sidechain=False,
        session_type_hint="cook",
    )

    assert tuple(field.name for field in dataclasses.fields(SessionSummary)) == (
        "backend_name",
        "session_id",
        "launch_id",
        "cwd",
        "first_prompt",
        "summary",
        "git_branch",
        "modified",
        "is_sidechain",
        "session_type_hint",
    )
    assert typing.get_type_hints(SessionSummary) == {
        "backend_name": str,
        "session_id": str,
        "launch_id": str | None,
        "cwd": str,
        "first_prompt": str,
        "summary": str,
        "git_branch": str | None,
        "modified": str | None,
        "is_sidechain": bool,
        "session_type_hint": str | None,
    }
    assert not hasattr(summary, "__dict__")
    with pytest.raises(FrozenInstanceError):
        summary.summary = "changed"  # type: ignore[misc]


def test_cook_session_handle_contract_and_callback_delegation():
    from collections.abc import Callable

    from autoskillit.core import CookSessionHandle

    calls: list[tuple[str, int, int]] = []
    handle = CookSessionHandle(
        view_id="launch-1-attempt-2",
        pass_fds=(7, 11),
        _record_spawn=lambda pid, pgid: calls.append(("spawn", pid, pgid)),
        _record_reaped=lambda pid, pgid: calls.append(("reaped", pid, pgid)),
    )

    assert tuple(field.name for field in dataclasses.fields(CookSessionHandle)) == (
        "view_id",
        "pass_fds",
        "_record_spawn",
        "_record_reaped",
    )
    hints = typing.get_type_hints(CookSessionHandle)
    assert hints == {
        "view_id": str,
        "pass_fds": tuple[int, ...],
        "_record_spawn": Callable[[int, int], None],
        "_record_reaped": Callable[[int, int], None],
    }
    assert not hasattr(handle, "__dict__")
    assert repr(handle) == "CookSessionHandle(view_id='launch-1-attempt-2', pass_fds=(7, 11))"

    equivalent = CookSessionHandle(
        view_id=handle.view_id,
        pass_fds=handle.pass_fds,
        _record_spawn=lambda _pid, _pgid: None,
        _record_reaped=lambda _pid, _pgid: None,
    )
    assert equivalent == handle

    handle.record_spawn(101, 202)
    handle.record_reaped(101, 202)
    assert calls == [("spawn", 101, 202), ("reaped", 101, 202)]
    with pytest.raises(FrozenInstanceError):
        handle.view_id = "other"  # type: ignore[misc]


def test_backend_module_all_exhaustive():
    from autoskillit.core.types._type_backend import __all__

    assert set(__all__) == {
        "ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS",
        "BackendCapabilities",
        "BackendConventions",
        "CLAUDE_CODE_CAPABILITIES",
        "CLAUDE_MODEL_ALIASES",
        "CODEX_EFFORT_MAPPING",
        "CODEX_MODEL_ALIASES",
        "CODEX_MODEL_ALIASES_LAST_VERIFIED",
        "CODEX_VALID_REASONING_EFFORTS",
        "CODEX_VALID_MODEL_IDS",
        "CmdOrigin",
        "CmdSpec",
        "CookSessionHandle",
        "ExecutableLaunchBinding",
        "ModelTranslation",
        "SessionSummary",
        "SKILL_MODEL_CLASSES",
        "SKILL_REASONING_EFFORTS",
        "SkillSessionConfig",
        "ClaudeEventData",
        "CodexEventData",
        "SessionEvent",
        "AgentSessionResult",
        "is_valid_codex_model_id",
        "model_class",
        "strip_context_window_suffix",
    }


def test_backend_conventions_frozen_slots_fields():
    import typing
    from dataclasses import FrozenInstanceError
    from pathlib import Path

    from autoskillit.core.types._type_backend import BackendConventions

    assert hasattr(BackendConventions, "__slots__")

    inst = BackendConventions(
        skills_subdir=Path("/claude/skills"),
        project_local_skill_search_dirs=(".claude/skills",),
    )
    assert inst.skills_subdir == Path("/claude/skills")
    assert inst.project_local_skill_search_dirs == (".claude/skills",)
    assert inst.persistent_session_root_subdir is None

    with pytest.raises(FrozenInstanceError):
        inst.skills_subdir = Path("/other")  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        inst.project_local_skill_search_dirs = (".other/skills",)  # type: ignore[misc]

    hints = typing.get_type_hints(BackendConventions)
    assert hints["skills_subdir"] is Path
    assert hints["project_local_skill_search_dirs"] == tuple[str, ...]
    assert hints["persistent_session_root_subdir"] == Path | None


def test_no_autoskillit_imports_in_backend():
    from autoskillit.core import paths

    backend_path = paths.pkg_root() / "core" / "types" / "_type_backend.py"
    source = backend_path.read_text()
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("from autoskillit") or stripped.startswith("import autoskillit"):
            pytest.fail(f"IL-0 violation: {stripped}")


def test_skill_session_config_importable_from_core():
    import dataclasses

    from autoskillit.core import SkillSessionConfig

    assert dataclasses.is_dataclass(SkillSessionConfig)


def test_skill_session_config_frozen():
    from dataclasses import FrozenInstanceError

    from autoskillit.core import SkillSessionConfig

    cfg = SkillSessionConfig()
    with pytest.raises(FrozenInstanceError):
        cfg.completion_marker = "x"  # type: ignore[misc]


def test_skill_session_config_slots():
    from autoskillit.core import SkillSessionConfig

    assert hasattr(SkillSessionConfig, "__slots__")
    cfg = SkillSessionConfig()
    assert not hasattr(cfg, "__dict__")


def test_skill_session_config_fields_exhaustive():
    import dataclasses

    from autoskillit.core import SkillSessionConfig

    fields = {f.name for f in dataclasses.fields(SkillSessionConfig)}
    assert fields == {
        "completion_marker",
        "model",
        "plugin_binding",
        "output_format",
        "add_dirs",
        "exit_after_stop_delay_ms",
        "stream_idle_timeout_ms",
        "mcp_tool_timeout_sec",
        "scenario_step_name",
        "temp_dir_relpath",
        "allowed_write_prefix",
        "allowed_write_prefixes",
        "provider_extras",
        "profile_name",
        "resume_session_id",
        "resume_checkpoint",
        "resume_message",
        "force_inactive_agent_teams",
        "sandbox_mode",
        "network_access",
        "include_scope_discipline",
        "native_shell_capture_decision",
        "managed_lineage_ref",
        "managed_attempt_id",
    }


def test_skill_session_config_defaults():
    from autoskillit.core import OutputFormat, SkillSessionConfig

    cfg = SkillSessionConfig()
    assert cfg.completion_marker == ""
    assert cfg.model is None
    assert cfg.plugin_binding is None
    assert cfg.output_format == OutputFormat.JSON
    assert cfg.add_dirs == ()
    assert cfg.exit_after_stop_delay_ms == 0
    assert cfg.stream_idle_timeout_ms == 0
    assert cfg.scenario_step_name == ""
    assert cfg.temp_dir_relpath is None
    assert cfg.allowed_write_prefix == ""
    assert cfg.allowed_write_prefixes == ()
    assert cfg.provider_extras is None
    assert cfg.profile_name == ""
    assert cfg.resume_session_id == ""
    assert cfg.resume_checkpoint is None
    assert cfg.resume_message is None
    assert cfg.sandbox_mode == "workspace-write"


def test_skill_session_config_field_types():
    import typing
    from collections.abc import Mapping

    from autoskillit.core import (
        OutputFormat,
        PluginLaunchBinding,
        SessionCheckpoint,
        SkillSessionConfig,
        ValidatedAddDir,
    )

    hints = typing.get_type_hints(SkillSessionConfig)
    assert hints["completion_marker"] is str
    assert hints["model"] == str | None
    assert hints["plugin_binding"] == PluginLaunchBinding | None
    assert hints["output_format"] is OutputFormat
    assert hints["add_dirs"] == tuple[ValidatedAddDir, ...]
    assert hints["exit_after_stop_delay_ms"] is int
    assert hints["stream_idle_timeout_ms"] is int
    assert hints["scenario_step_name"] is str
    assert hints["temp_dir_relpath"] == str | None
    assert hints["allowed_write_prefix"] is str
    assert hints["allowed_write_prefixes"] == tuple[str, ...]
    assert hints["provider_extras"] == Mapping[str, str] | None
    assert hints["profile_name"] is str
    assert hints["resume_session_id"] is str
    assert hints["resume_checkpoint"] == SessionCheckpoint | None
    assert hints["resume_message"] == str | None
    assert hints["sandbox_mode"] is str
