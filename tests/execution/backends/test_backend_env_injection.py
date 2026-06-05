"""Parametrized assertions: both builders inject AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND__BACKEND", raising=False)


def test_skill_session_cmd_injects_write_guard_tool_names() -> None:
    from autoskillit.execution.backends import BACKEND_REGISTRY

    assert BACKEND_REGISTRY, "BACKEND_REGISTRY is empty"
    for name, cls in BACKEND_REGISTRY.items():
        backend = cls()
        spec = backend.build_skill_session_cmd(
            "/autoskillit:investigate", "/repo", completion_marker="DONE"
        )
        assert "AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES" in spec.env, (
            f"{name}: AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES missing from build_skill_session_cmd env"
        )
        assert spec.env["AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES"], (
            f"{name}: AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES is empty in build_skill_session_cmd env"
        )


def test_food_truck_cmd_injects_write_guard_tool_names() -> None:
    from autoskillit.core import DirectInstall
    from autoskillit.execution.backends import BACKEND_REGISTRY

    assert BACKEND_REGISTRY, "BACKEND_REGISTRY is empty"
    for name, cls in BACKEND_REGISTRY.items():
        backend = cls()
        spec = backend.build_food_truck_cmd(
            orchestrator_prompt="test prompt",
            plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
            cwd="/repo",
            completion_marker="DONE",
        )
        assert "AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES" in spec.env, (
            f"{name}: AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES missing from build_food_truck_cmd env"
        )
        assert spec.env["AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES"], (
            f"{name}: AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES is empty in build_food_truck_cmd env"
        )
