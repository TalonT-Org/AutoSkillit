"""Tests for CmdSpec, ClaudeEventData, CodexEventData, SessionEvent, AgentSessionResult."""

from __future__ import annotations

import dataclasses
import typing
from dataclasses import FrozenInstanceError

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_cmd_spec_frozen():
    from autoskillit.core import CmdSpec

    spec = CmdSpec(cmd=["claude", "--model", "opus"], env={"FOO": "bar"})
    with pytest.raises(FrozenInstanceError):
        spec.cmd = []


def test_cmd_spec_fields():
    from autoskillit.core import CmdSpec

    fields = {f.name for f in dataclasses.fields(CmdSpec)}
    assert fields == {"cmd", "env"}


def test_cmd_spec_env_accepts_mapping():
    from collections.abc import Mapping

    from autoskillit.core import CmdSpec

    hints = typing.get_type_hints(CmdSpec)
    assert hints["env"] == Mapping[str, str]


def test_cmd_spec_slots():
    from autoskillit.core import CmdSpec

    spec = CmdSpec(cmd=["x"], env={})
    assert not hasattr(spec, "__dict__")


def test_claude_event_data_frozen():
    from autoskillit.core import ClaudeEventData

    ev = ClaudeEventData(record_type="assistant", subtype="text", session_id="s1")
    with pytest.raises(FrozenInstanceError):
        ev.record_type = "x"


def test_claude_event_data_raw_default():
    from autoskillit.core import ClaudeEventData

    ev = ClaudeEventData(record_type="r", subtype="s", session_id="id")
    assert ev.raw == {}


def test_codex_event_data_frozen():
    from autoskillit.core import CodexEventData

    ev = CodexEventData(record_type="item", thread_id="t1", item_type="msg")
    with pytest.raises(FrozenInstanceError):
        ev.record_type = "x"


def test_codex_event_data_raw_default():
    from autoskillit.core import CodexEventData

    ev = CodexEventData(record_type="r", thread_id="t", item_type="i")
    assert ev.raw == {}


def test_session_event_frozen():
    from autoskillit.core import BackendEventKind, SessionEvent

    ev = SessionEvent(kind=BackendEventKind.COMPLETION, is_terminal=False, has_marker=False)
    with pytest.raises(FrozenInstanceError):
        ev.kind = BackendEventKind.ERROR


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
        r.success = False


def test_agent_session_result_raw_default():
    from autoskillit.core import AgentSessionResult

    r = AgentSessionResult(
        success=True, exit_code=0, session_id="s", backend_name="b", elapsed_seconds=1.0
    )
    assert r.output == ""
    assert r.error == ""
    assert r.raw == {}


def test_all_new_dataclasses_in_backend_module_all():
    from autoskillit.core.types._type_backend import __all__

    for name in [
        "CmdSpec",
        "ClaudeEventData",
        "CodexEventData",
        "SessionEvent",
        "AgentSessionResult",
    ]:
        assert name in __all__


def test_no_autoskillit_imports_in_backend():
    from autoskillit.core import paths

    backend_path = paths.pkg_root() / "core" / "types" / "_type_backend.py"
    source = backend_path.read_text()
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("from autoskillit") or stripped.startswith("import autoskillit"):
            pytest.fail(f"IL-0 violation: {stripped}")
