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

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("session_type", "expected"),
    [
        ("skill", "no_session_bound"),
        ("orchestrator", "ineligible_session_type"),
        ("fleet", "ineligible_session_type"),
    ],
)
async def test_kitchen_status_reports_broker_authority(
    session_type, expected, tool_ctx_kitchen_open, monkeypatch
):
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", session_type)
    from autoskillit.server.tools.tools_status import kitchen_status

    result = json.loads(await kitchen_status())

    assert result["broker_authority"] == expected
