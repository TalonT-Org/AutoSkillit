"""kitchen_status reports broker_authority (#4684 Fix D).

Before this field, a caller could not introspect broker eligibility except
by calling enable_exploration and observing a failure code, or by dispatching
a downstream broker-only subagent and observing the zero-tool refusal. This
matrix pins the field for the session types EXPLORER_INELIGIBLE_SESSION_TYPES
excludes and includes.
"""

from __future__ import annotations

import json

import pytest

from autoskillit.core import ExplorationFailureCode

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("session_type", "expected"),
    [
        ("skill", "no_session_bound"),
        ("orchestrator", "session_type_ineligible"),
        ("fleet", "session_type_ineligible"),
    ],
)
async def test_kitchen_status_reports_broker_authority(
    session_type, expected, tool_ctx_kitchen_open, monkeypatch
):
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", session_type)
    from autoskillit.server.tools.tools_status import kitchen_status

    result = json.loads(await kitchen_status())

    assert result["broker_authority"] == expected


@pytest.mark.anyio
async def test_kitchen_status_reports_store_unavailable_when_store_is_not_owner_bound(
    tool_ctx_kitchen_open, monkeypatch
):
    """A misconfigured or unbound store must report STORE_UNAVAILABLE, never a
    literal outside the ExplorationFailureCode registry contract's scan scope."""
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
    monkeypatch.setattr(tool_ctx_kitchen_open, "exploration_context_store", None)
    from autoskillit.server.tools.tools_status import kitchen_status

    result = json.loads(await kitchen_status())

    assert result["broker_authority"] == ExplorationFailureCode.STORE_UNAVAILABLE.value


@pytest.mark.anyio
async def test_kitchen_status_reports_available_with_an_active_session_scoped_binding(
    tool_ctx_kitchen_open, monkeypatch, tmp_path
):
    """The only state the pre-existing stub could never report: broker actually available."""
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
    from autoskillit.server.tools.tools_status import kitchen_status

    store = tool_ctx_kitchen_open.exploration_context_store
    store.bind_session_scoped(
        owner_id="uid:test",
        session_id="test-session",
        cwd=tmp_path,
        repository_root=store.trusted_root,
        source_identity="interactive:test-session",
    )

    result = json.loads(await kitchen_status())

    assert result["broker_authority"] == "available"
