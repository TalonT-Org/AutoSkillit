"""Tests for BackendEventKind StrEnum — member exhaustiveness, values, importability."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_backend_event_kind_is_str_enum():
    from autoskillit.core import BackendEventKind

    assert issubclass(BackendEventKind, str)


def test_backend_event_kind_members():
    from autoskillit.core import BackendEventKind

    assert set(BackendEventKind) == {
        BackendEventKind.COMPLETION,
        BackendEventKind.SESSION_META,
        BackendEventKind.TOOL_OUTPUT,
        BackendEventKind.ERROR,
        BackendEventKind.IGNORED,
    }


def test_backend_event_kind_values():
    from autoskillit.core import BackendEventKind

    assert BackendEventKind.COMPLETION == "completion"
    assert BackendEventKind.SESSION_META == "session_meta"
    assert BackendEventKind.TOOL_OUTPUT == "tool_output"
    assert BackendEventKind.ERROR == "error"
    assert BackendEventKind.IGNORED == "ignored"


def test_backend_event_kind_in_enums_all():
    from autoskillit.core.types._type_enums import __all__

    assert "BackendEventKind" in __all__


def test_backend_event_kind_importable_from_core():
    from autoskillit.core import BackendEventKind  # noqa: F401
