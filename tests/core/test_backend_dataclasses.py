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
    assert fields == {"cmd", "env", "cwd", "origin", "is_resume"}


def test_cmd_spec_is_resume_default():
    from autoskillit.core import CmdSpec

    spec = CmdSpec(cmd=(), env={})
    assert spec.is_resume is False


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


def test_backend_module_all_exhaustive():
    from autoskillit.core.types._type_backend import __all__

    assert set(__all__) == {
        "BackendCapabilities",
        "BackendConventions",
        "CLAUDE_CODE_CAPABILITIES",
        "CLAUDE_MODEL_ALIASES",
        "CODEX_MODEL_ALIASES",
        "CmdOrigin",
        "CmdSpec",
        "SkillSessionConfig",
        "ClaudeEventData",
        "CodexEventData",
        "SessionEvent",
        "AgentSessionResult",
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

    with pytest.raises(FrozenInstanceError):
        inst.skills_subdir = Path("/other")  # type: ignore[misc]

    hints = typing.get_type_hints(BackendConventions)
    assert hints["skills_subdir"] is Path
    assert hints["project_local_skill_search_dirs"] == tuple[str, ...]


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
        "plugin_source",
        "output_format",
        "add_dirs",
        "exit_after_stop_delay_ms",
        "stream_idle_timeout_ms",
        "scenario_step_name",
        "temp_dir_relpath",
        "allowed_write_prefix",
        "allowed_write_prefixes",
        "provider_extras",
        "profile_name",
        "resume_session_id",
        "resume_checkpoint",
        "resume_message",
        "backend_override",
    }


def test_skill_session_config_defaults():
    from autoskillit.core import OutputFormat, SkillSessionConfig

    cfg = SkillSessionConfig()
    assert cfg.completion_marker == ""
    assert cfg.model is None
    assert cfg.plugin_source is None
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
    assert cfg.backend_override is None


def test_skill_session_config_field_types():
    import typing
    from collections.abc import Mapping

    from autoskillit.core import (
        OutputFormat,
        PluginSource,
        SessionCheckpoint,
        SkillSessionConfig,
        ValidatedAddDir,
    )

    hints = typing.get_type_hints(SkillSessionConfig)
    assert hints["completion_marker"] is str
    assert hints["model"] == str | None
    assert hints["plugin_source"] == PluginSource | None
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
    assert hints["backend_override"] == str | None
