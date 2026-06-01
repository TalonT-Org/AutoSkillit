"""Architectural invariant: skill and food-truck builders must set the same required base env vars."""  # noqa: E501

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_REQUIRED_IN_BOTH: frozenset[str] = frozenset(
    {
        "AUTOSKILLIT_HEADLESS",
        "AUTOSKILLIT_SESSION_TYPE",
        "MAX_MCP_OUTPUT_TOKENS",
    }
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)


def test_skill_and_food_truck_share_required_env_vars() -> None:
    """build_skill_session_cmd and build_food_truck_cmd must both set the required base env vars."""  # noqa: E501
    from autoskillit.core.types._type_plugin_source import DirectInstall
    from autoskillit.execution.backends import BACKEND_REGISTRY

    assert BACKEND_REGISTRY, "BACKEND_REGISTRY is empty — test provides no coverage"
    for name, cls in BACKEND_REGISTRY.items():
        backend = cls()
        skill_spec = backend.build_skill_session_cmd(
            "/autoskillit:investigate", "/repo", completion_marker="DONE"
        )
        food_truck_spec = backend.build_food_truck_cmd(
            orchestrator_prompt="test prompt",
            plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
            cwd="/repo",
            completion_marker="DONE",
        )
        for var in _REQUIRED_IN_BOTH:
            assert var in skill_spec.env, f"{name}: {var} missing from build_skill_session_cmd env"
            assert var in food_truck_spec.env, (
                f"{name}: {var} missing from build_food_truck_cmd env"
            )


def test_resume_cmd_has_baseline_env() -> None:
    """build_resume_cmd must include MAX_MCP_OUTPUT_TOKENS (from _SESSION_BASELINE_ENV)."""
    from autoskillit.execution.backends import BACKEND_REGISTRY

    assert BACKEND_REGISTRY, "BACKEND_REGISTRY is empty — test provides no coverage"
    for name, cls in BACKEND_REGISTRY.items():
        backend = cls()
        if not backend.capabilities.session_resume_capable:
            continue
        resume_spec = backend.build_resume_cmd(resume_session_id="test-session", prompt="continue")
        assert "MAX_MCP_OUTPUT_TOKENS" in resume_spec.env, (
            f"{name}: MAX_MCP_OUTPUT_TOKENS missing from build_resume_cmd env"
        )


def test_interactive_cmd_has_baseline_env() -> None:
    """build_interactive_cmd must include MAX_MCP_OUTPUT_TOKENS (from _SESSION_BASELINE_ENV)."""
    from autoskillit.execution.backends import BACKEND_REGISTRY

    assert BACKEND_REGISTRY, "BACKEND_REGISTRY is empty — test provides no coverage"
    for name, cls in BACKEND_REGISTRY.items():
        backend = cls()
        spec = backend.build_interactive_cmd()
        assert "MAX_MCP_OUTPUT_TOKENS" in spec.env, (
            f"{name}: MAX_MCP_OUTPUT_TOKENS missing from build_interactive_cmd env"
        )


def test_agent_backend_env_var_in_food_truck(monkeypatch: pytest.MonkeyPatch) -> None:
    """AUTOSKILLIT_AGENT_BACKEND must appear in build_food_truck_cmd env for every backend."""
    from autoskillit.core.types._type_plugin_source import DirectInstall
    from autoskillit.execution.backends import BACKEND_REGISTRY

    # Ensure clean environment - remove any residual AGENT_BACKEND_ENV_VAR from host
    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)

    assert BACKEND_REGISTRY, "BACKEND_REGISTRY is empty — test provides no coverage"
    for name, cls in BACKEND_REGISTRY.items():
        backend = cls()
        food_truck_spec = backend.build_food_truck_cmd(
            orchestrator_prompt="test prompt",
            plugin_source=DirectInstall(plugin_dir=Path("/plugins")),
            cwd="/repo",
            completion_marker="DONE",
        )
        assert "AUTOSKILLIT_AGENT_BACKEND" in food_truck_spec.env, (
            f"{name}: AUTOSKILLIT_AGENT_BACKEND missing from build_food_truck_cmd env"
        )
