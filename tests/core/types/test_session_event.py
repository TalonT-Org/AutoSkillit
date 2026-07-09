"""SessionEvent frozen dataclass behavior."""

from __future__ import annotations

import dataclasses

import pytest

from autoskillit.core.types._type_backend import (
    ClaudeEventData,
    CodexEventData,
    SessionEvent,
)
from autoskillit.core.types._type_enums import BackendEventKind

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_required_fields_present() -> None:
    field_names = {f.name for f in dataclasses.fields(SessionEvent)}
    assert field_names == {
        "kind",
        "is_terminal",
        "has_marker",
        "session_id",
        "exit_code",
        "backend_data",
    }


def test_construction_stores_all_fields() -> None:
    claude_data = ClaudeEventData(record_type="assistant", subtype="text", session_id="s1")
    ev = SessionEvent(
        kind=BackendEventKind.COMPLETION,
        is_terminal=True,
        has_marker=True,
        session_id="abc",
        exit_code=0,
        backend_data=claude_data,
    )
    assert ev.kind is BackendEventKind.COMPLETION
    assert ev.is_terminal is True
    assert ev.has_marker is True
    assert ev.session_id == "abc"
    assert ev.exit_code == 0
    assert ev.backend_data is claude_data


def test_mutation_raises_attribute_error() -> None:
    ev = SessionEvent(kind=BackendEventKind.COMPLETION, is_terminal=False, has_marker=False)
    with pytest.raises(AttributeError):
        ev.kind = BackendEventKind.ERROR  # type: ignore[misc]


def test_default_construction_succeeds() -> None:
    ev = SessionEvent(kind=BackendEventKind.ERROR, is_terminal=True, has_marker=False)
    assert ev.session_id is None
    assert ev.exit_code is None
    assert ev.backend_data is None


def test_two_identical_instances_are_equal() -> None:
    kwargs = dict(
        kind=BackendEventKind.SESSION_META,
        is_terminal=False,
        has_marker=True,
        session_id="s1",
        exit_code=1,
        backend_data=None,
    )
    a = SessionEvent(**kwargs)
    b = SessionEvent(**kwargs)
    assert a == b
    assert a is not b


def test_backend_data_accepts_none() -> None:
    ev = SessionEvent(
        kind=BackendEventKind.IGNORED,
        is_terminal=False,
        has_marker=False,
        backend_data=None,
    )
    assert ev.backend_data is None


def test_backend_data_accepts_claude_event_data() -> None:
    data = ClaudeEventData(record_type="assistant", subtype="text", session_id="s1")
    ev = SessionEvent(
        kind=BackendEventKind.COMPLETION,
        is_terminal=True,
        has_marker=True,
        backend_data=data,
    )
    assert ev.backend_data is data
    assert isinstance(ev.backend_data, ClaudeEventData)
    assert ev.backend_data.record_type == "assistant"


def test_backend_data_accepts_codex_event_data() -> None:
    data = CodexEventData(record_type="item", thread_id="t1", item_type="msg")
    ev = SessionEvent(
        kind=BackendEventKind.TOOL_OUTPUT,
        is_terminal=False,
        has_marker=False,
        backend_data=data,
    )
    assert ev.backend_data is data
    assert isinstance(ev.backend_data, CodexEventData)
    assert ev.backend_data.thread_id == "t1"


def test_session_event_importable_from_core_types() -> None:
    from autoskillit.core.types import SessionEvent as PublicSessionEvent

    assert PublicSessionEvent is SessionEvent
