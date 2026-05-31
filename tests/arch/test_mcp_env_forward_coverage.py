"""Architectural invariant: mcp_env_forward_vars must appear in CmdSpec.env for all builders."""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_MCP_CLIENT_BACKEND", raising=False)


def test_mcp_env_forward_vars_in_skill_session_cmd() -> None:
    """mcp_env_forward_vars must appear in build_skill_session_cmd env."""
    from autoskillit.execution.backends import BACKEND_REGISTRY

    for name, cls in BACKEND_REGISTRY.items():
        backend = cls()
        if not backend.capabilities.mcp_env_forward_vars:
            continue
        spec = backend.build_skill_session_cmd(
            "/autoskillit:investigate", "/repo", completion_marker="DONE"
        )
        for var in backend.capabilities.mcp_env_forward_vars:
            assert var in spec.env, f"{name}: {var} missing from build_skill_session_cmd env"


def test_mcp_env_forward_vars_in_food_truck_cmd() -> None:
    """mcp_env_forward_vars must appear in build_food_truck_cmd env."""
    from autoskillit.core.types._type_plugin_source import DirectInstall
    from autoskillit.execution.backends import BACKEND_REGISTRY

    for name, cls in BACKEND_REGISTRY.items():
        backend = cls()
        if not backend.capabilities.mcp_env_forward_vars:
            continue
        spec = backend.build_food_truck_cmd(
            orchestrator_prompt="test prompt",
            plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
            cwd="/repo",
            completion_marker="DONE",
        )
        for var in backend.capabilities.mcp_env_forward_vars:
            assert var in spec.env, f"{name}: {var} missing from build_food_truck_cmd env"


def test_mcp_env_forward_vars_in_headless_cmd() -> None:
    """mcp_env_forward_vars must appear in build_headless_cmd env (if method exists)."""
    from autoskillit.execution.backends import BACKEND_REGISTRY

    for name, cls in BACKEND_REGISTRY.items():
        backend = cls()
        if not backend.capabilities.mcp_env_forward_vars:
            continue
        if not hasattr(backend, "build_headless_cmd"):
            continue
        spec = backend.build_headless_cmd(prompt="test")
        for var in backend.capabilities.mcp_env_forward_vars:
            assert var in spec.env, f"{name}: {var} missing from build_headless_cmd env"


def test_mcp_env_forward_vars_in_resume_cmd() -> None:
    """mcp_env_forward_vars must appear in build_resume_cmd env."""
    from autoskillit.execution.backends import BACKEND_REGISTRY

    for name, cls in BACKEND_REGISTRY.items():
        backend = cls()
        if not backend.capabilities.mcp_env_forward_vars:
            continue
        if not backend.capabilities.session_resume_capable:
            continue
        spec = backend.build_resume_cmd(resume_session_id="test-session", prompt="continue")
        for var in backend.capabilities.mcp_env_forward_vars:
            assert var in spec.env, f"{name}: {var} missing from build_resume_cmd env"


def test_mcp_env_forward_vars_in_interactive_cmd() -> None:
    """mcp_env_forward_vars must appear in build_interactive_cmd env."""
    from autoskillit.execution.backends import BACKEND_REGISTRY

    for name, cls in BACKEND_REGISTRY.items():
        backend = cls()
        if not backend.capabilities.mcp_env_forward_vars:
            continue
        spec = backend.build_interactive_cmd()
        for var in backend.capabilities.mcp_env_forward_vars:
            assert var in spec.env, f"{name}: {var} missing from build_interactive_cmd env"
