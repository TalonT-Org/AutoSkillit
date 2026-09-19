"""Session-scope admission contracts for registered MCP tools."""

from __future__ import annotations

import pytest

from autoskillit.core import SessionShape, SessionType
from autoskillit.server.lifecycle._session_scope import (
    SCOPE_FLEET,
    SCOPE_ORCHESTRATOR_EXACT,
    SCOPE_ORCHESTRATOR_OR_HIGHER,
    admit_tool_session_scope,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.parametrize(
    ("scope", "shape", "admitted"),
    [
        (SCOPE_ORCHESTRATOR_EXACT, SessionShape(True, SessionType.ORCHESTRATOR), True),
        (SCOPE_ORCHESTRATOR_EXACT, SessionShape(True, SessionType.FLEET), False),
        (SCOPE_ORCHESTRATOR_OR_HIGHER, SessionShape(True, SessionType.FLEET), True),
        (SCOPE_FLEET, SessionShape(False, SessionType.SKILL), False),
    ],
)
def test_tool_scope_admission(scope, shape, admitted) -> None:
    result = admit_tool_session_scope("tool", scope, shape)

    assert (result is None) is admitted
