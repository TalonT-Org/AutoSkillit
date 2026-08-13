"""Conservative Claude attestation contracts for unprobed headless builders."""

from __future__ import annotations

import pytest

from autoskillit.core import AUTOSKILLIT_ATTESTED_META_SUPPORT
from autoskillit.execution.backends import ClaudeCodeBackend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


@pytest.mark.parametrize("attested", [None, "1"])
@pytest.mark.parametrize("builder", ["resume", "food-truck", "skill-session"])
def test_headless_builders_default_conservative_and_honor_explicit_attestation(
    builder: str,
    attested: str | None,
) -> None:
    backend = ClaudeCodeBackend()
    extras = None if attested is None else {AUTOSKILLIT_ATTESTED_META_SUPPORT: attested}

    if builder == "resume":
        spec = backend.build_resume_cmd(
            resume_session_id="session-1",
            prompt="continue",
            env_extras=extras,
        )
    elif builder == "food-truck":
        spec = backend.build_food_truck_cmd(
            orchestrator_prompt="dispatch",
            plugin_binding=None,
            cwd="/work",
            completion_marker="done",
            env_extras=extras,
        )
    else:
        spec = backend.build_skill_session_cmd(
            "/autoskillit:test",
            "/work",
            completion_marker="done",
            provider_extras=extras,
        )

    assert spec.env[AUTOSKILLIT_ATTESTED_META_SUPPORT] == (attested or "0")
