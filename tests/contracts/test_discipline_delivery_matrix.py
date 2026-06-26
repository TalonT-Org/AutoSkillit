"""Contract: session-type x backend discipline-delivery channel matrix.

Encodes the 8-case (SessionType x Backend) matrix asserting that the PRIMARY
delivery channel is populated for each combination, and that SESSION_TYPE env
is correctly set where applicable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.cli._prompts import (
    _build_fleet_dispatch_prompt,
    _build_open_kitchen_prompt,
    _build_orchestrator_prompt,
)
from autoskillit.core import (
    SESSION_TYPE_ENV_VAR,
    SESSION_TYPE_FLEET,
    SESSION_TYPE_ORCHESTRATOR,
    SESSION_TYPE_SKILL,
)
from autoskillit.core.types import DirectInstall
from autoskillit.execution.backends import ClaudeCodeBackend
from autoskillit.execution.backends.codex import CodexBackend  # noqa: F401 — triggers registration

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

DELIVERY_CHANNEL_CASES: list[tuple[str, str]] = [
    ("fleet", "claude-code"),
    ("fleet", "codex"),
    ("orchestrator-interactive", "claude-code"),
    ("orchestrator-interactive", "codex"),
    ("orchestrator-headless", "claude-code"),
    ("orchestrator-headless", "codex"),
    ("skill", "claude-code"),
    ("skill", "codex"),
]

_SOUS_CHEF_SENTINEL = "NEVER read recipe YAML files from the filesystem"

_FOOD_TRUCK_BASE = {
    "orchestrator_prompt": "headless orchestrator discipline",
    "plugin_source": DirectInstall(plugin_dir=Path("/tmp")),
    "cwd": "/tmp",
    "completion_marker": "%%TEST%%",
}


def _backend_for(name: str) -> ClaudeCodeBackend | CodexBackend:
    return ClaudeCodeBackend() if name == "claude-code" else CodexBackend()


def _assert_interactive_channel(spec: object, backend_name: str, content: str) -> None:
    """Assert the primary interactive delivery channel is populated."""
    cmd = spec.cmd  # type: ignore[attr-defined]
    if backend_name == "claude-code":
        assert "--append-system-prompt" in cmd
        idx = cmd.index("--append-system-prompt")
        assert cmd[idx + 1] == content
    else:
        assert any(f"developer_instructions={content}" in arg for arg in cmd)


def test_delivery_channel_cases_count() -> None:
    assert len(DELIVERY_CHANNEL_CASES) == 8


@pytest.mark.parametrize(
    ("session_type", "backend_name"),
    DELIVERY_CHANNEL_CASES,
    ids=[f"{st}-{bn}" for st, bn in DELIVERY_CHANNEL_CASES],
)
def test_primary_channel_populated(
    session_type: str, backend_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
    backend = _backend_for(backend_name)

    if session_type == "fleet":
        spec = backend.build_interactive_cmd(
            system_prompt="fleet discipline content",
            env_extras={SESSION_TYPE_ENV_VAR: SESSION_TYPE_FLEET},
        )
        _assert_interactive_channel(spec, backend_name, "fleet discipline content")
        assert spec.env[SESSION_TYPE_ENV_VAR] == SESSION_TYPE_FLEET

    elif session_type == "orchestrator-interactive":
        spec = backend.build_interactive_cmd(
            system_prompt="orchestrator discipline content",
        )
        _assert_interactive_channel(spec, backend_name, "orchestrator discipline content")

    elif session_type == "orchestrator-headless":
        spec = backend.build_food_truck_cmd(**_FOOD_TRUCK_BASE)
        assert any("headless orchestrator discipline" in arg for arg in spec.cmd)
        assert spec.env[SESSION_TYPE_ENV_VAR] == SESSION_TYPE_ORCHESTRATOR

    elif session_type == "skill":
        spec = backend.build_skill_session_cmd("/test-skill", "/tmp")
        assert any("test-skill" in arg for arg in spec.cmd)
        assert spec.env[SESSION_TYPE_ENV_VAR] == SESSION_TYPE_SKILL

    else:
        pytest.fail(f"Unknown session type: {session_type}")


class TestSousChefContentPresence:
    """Assert _read_full_sous_chef content in orchestrator/kitchen prompts but not fleet."""

    def test_sous_chef_in_orchestrator_prompt(self) -> None:
        prompt = _build_orchestrator_prompt("test-recipe", "mcp__autoskillit__")
        assert _SOUS_CHEF_SENTINEL in prompt

    def test_sous_chef_in_open_kitchen_prompt(self) -> None:
        prompt = _build_open_kitchen_prompt("mcp__autoskillit__")
        assert _SOUS_CHEF_SENTINEL in prompt

    def test_sous_chef_not_in_fleet_dispatch_prompt(self) -> None:
        prompt = _build_fleet_dispatch_prompt("mcp__autoskillit__")
        assert _SOUS_CHEF_SENTINEL not in prompt
