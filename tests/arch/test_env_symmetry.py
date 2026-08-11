"""Architectural invariant: skill and food-truck builders must set the same required base env vars."""  # noqa: E501

from pathlib import Path

import pytest

from tests.execution.backends._plugin_binding import plugin_binding

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_REQUIRED_IN_BOTH: frozenset[str] = frozenset(
    {
        "AUTOSKILLIT_APPLICABLE_GUARDS",
        "AUTOSKILLIT_ATTESTED_CLIENT_GATE_TOKENS",
        "AUTOSKILLIT_ATTESTED_META_SUPPORT",
        "AUTOSKILLIT_HEADLESS",
        "AUTOSKILLIT_SESSION_TYPE",
        "AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES",
        "MAX_MCP_OUTPUT_TOKENS",
    }
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND__BACKEND", raising=False)


def test_skill_and_food_truck_share_required_env_vars() -> None:
    """build_skill_session_cmd and build_food_truck_cmd must both set the required base env vars."""  # noqa: E501
    from autoskillit.execution.backends import BACKEND_REGISTRY

    assert BACKEND_REGISTRY, "BACKEND_REGISTRY is empty — test provides no coverage"
    for name, cls in BACKEND_REGISTRY.items():
        backend = cls()
        skill_spec = backend.build_skill_session_cmd(
            "/autoskillit:investigate", "/repo", completion_marker="DONE"
        )
        with plugin_binding(Path("/plugins")) as binding:
            food_truck_spec = backend.build_food_truck_cmd(
                orchestrator_prompt="test prompt",
                plugin_binding=binding,
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
    from autoskillit.execution.backends import BACKEND_REGISTRY

    # Ensure clean environment - remove any residual AGENT_BACKEND_ENV_VAR from host
    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)

    assert BACKEND_REGISTRY, "BACKEND_REGISTRY is empty — test provides no coverage"
    for name, cls in BACKEND_REGISTRY.items():
        backend = cls()
        with plugin_binding(Path("/plugins")) as binding:
            food_truck_spec = backend.build_food_truck_cmd(
                orchestrator_prompt="test prompt",
                plugin_binding=binding,
                cwd="/repo",
                completion_marker="DONE",
            )
        assert "AUTOSKILLIT_AGENT_BACKEND" in food_truck_spec.env, (
            f"{name}: AUTOSKILLIT_AGENT_BACKEND missing from build_food_truck_cmd env"
        )


def test_dynaconf_backend_env_var_in_skill_session() -> None:
    """Nested AUTOSKILLIT_AGENT_BACKEND__BACKEND in build_skill_session_cmd env for every backend."""  # noqa: E501
    from autoskillit.execution.backends import BACKEND_REGISTRY

    assert BACKEND_REGISTRY, "BACKEND_REGISTRY is empty — test provides no coverage"
    for name, cls in BACKEND_REGISTRY.items():
        backend = cls()
        skill_spec = backend.build_skill_session_cmd(
            "/autoskillit:investigate", "/repo", completion_marker="DONE"
        )
        assert "AUTOSKILLIT_AGENT_BACKEND__BACKEND" in skill_spec.env, (
            f"{name}: AUTOSKILLIT_AGENT_BACKEND__BACKEND missing from build_skill_session_cmd env"
        )


def test_dynaconf_backend_env_var_in_food_truck_cmd() -> None:
    """Nested AUTOSKILLIT_AGENT_BACKEND__BACKEND in build_food_truck_cmd env for every backend."""  # noqa: E501
    from autoskillit.execution.backends import BACKEND_REGISTRY

    assert BACKEND_REGISTRY, "BACKEND_REGISTRY is empty — test provides no coverage"
    for name, cls in BACKEND_REGISTRY.items():
        backend = cls()
        with plugin_binding(Path("/plugins")) as binding:
            food_truck_spec = backend.build_food_truck_cmd(
                orchestrator_prompt="test prompt",
                plugin_binding=binding,
                cwd="/repo",
                completion_marker="DONE",
            )
        assert "AUTOSKILLIT_AGENT_BACKEND__BACKEND" in food_truck_spec.env, (
            f"{name}: AUTOSKILLIT_AGENT_BACKEND__BACKEND missing from build_food_truck_cmd env"
        )


def test_dynaconf_and_flat_backend_values_match() -> None:
    """Nested and flat AGENT_BACKEND env vars must carry the same value in all cmd builders."""
    from autoskillit.execution.backends import BACKEND_REGISTRY

    assert BACKEND_REGISTRY, "BACKEND_REGISTRY is empty — test provides no coverage"
    for name, cls in BACKEND_REGISTRY.items():
        backend = cls()
        skill_spec = backend.build_skill_session_cmd(
            "/autoskillit:investigate", "/repo", completion_marker="DONE"
        )
        assert (
            skill_spec.env["AUTOSKILLIT_AGENT_BACKEND__BACKEND"]
            == skill_spec.env["AUTOSKILLIT_AGENT_BACKEND"]
        ), f"{name}: nested and flat AGENT_BACKEND values differ in build_skill_session_cmd"
        with plugin_binding(Path("/plugins")) as binding:
            food_truck_spec = backend.build_food_truck_cmd(
                orchestrator_prompt="test prompt",
                plugin_binding=binding,
                cwd="/repo",
                completion_marker="DONE",
            )
        assert (
            food_truck_spec.env["AUTOSKILLIT_AGENT_BACKEND__BACKEND"]
            == food_truck_spec.env["AUTOSKILLIT_AGENT_BACKEND"]
        ), f"{name}: nested and flat AGENT_BACKEND values differ in build_food_truck_cmd"


_ALL_GUARD_BUILDERS: list[str] = [
    "build_skill_session_cmd",
    "build_food_truck_cmd",
    "build_interactive_cmd",
    "build_resume_cmd",
]


def _call_builder(backend: object, builder_name: str) -> object:
    if builder_name == "build_skill_session_cmd":
        return backend.build_skill_session_cmd(
            "/autoskillit:investigate", "/repo", completion_marker="DONE"
        )
    if builder_name == "build_food_truck_cmd":
        with plugin_binding(Path("/plugins")) as binding:
            return backend.build_food_truck_cmd(
                orchestrator_prompt="test prompt",
                plugin_binding=binding,
                cwd="/repo",
                completion_marker="DONE",
            )
    if builder_name == "build_interactive_cmd":
        return backend.build_interactive_cmd()
    if builder_name == "build_resume_cmd":
        return backend.build_resume_cmd(resume_session_id="test-session", prompt="continue")
    msg = f"Unknown builder: {builder_name}"
    raise ValueError(msg)


@pytest.mark.parametrize("builder_name", _ALL_GUARD_BUILDERS)
def test_agent_backend_flat_env_var_in_all_guard_launch_builders(builder_name: str) -> None:
    """AUTOSKILLIT_AGENT_BACKEND must appear in every builder's CmdSpec.env for every backend."""
    from autoskillit.core.types._type_backend import CmdSpec
    from autoskillit.execution.backends import BACKEND_REGISTRY

    assert BACKEND_REGISTRY, "BACKEND_REGISTRY is empty — test provides no coverage"
    for name, cls in BACKEND_REGISTRY.items():
        backend = cls()
        if builder_name == "build_resume_cmd" and not backend.capabilities.session_resume_capable:
            continue
        spec = _call_builder(backend, builder_name)
        assert isinstance(spec, CmdSpec), f"{name}: {builder_name} did not return CmdSpec"
        assert "AUTOSKILLIT_AGENT_BACKEND" in spec.env, (
            f"{name}: AUTOSKILLIT_AGENT_BACKEND missing from {builder_name} env"
        )


def test_agent_backend_flat_and_dynaconf_values_match_in_interactive_and_resume() -> None:
    """Nested and flat AGENT_BACKEND env vars must carry the same value in interactive and resume builders."""  # noqa: E501
    from autoskillit.execution.backends import BACKEND_REGISTRY

    assert BACKEND_REGISTRY, "BACKEND_REGISTRY is empty — test provides no coverage"
    for name, cls in BACKEND_REGISTRY.items():
        backend = cls()
        interactive_spec = backend.build_interactive_cmd()
        assert (
            interactive_spec.env["AUTOSKILLIT_AGENT_BACKEND__BACKEND"]
            == interactive_spec.env["AUTOSKILLIT_AGENT_BACKEND"]
        ), f"{name}: nested and flat AGENT_BACKEND values differ in build_interactive_cmd"
        if not backend.capabilities.session_resume_capable:
            continue
        resume_spec = backend.build_resume_cmd(resume_session_id="test-session", prompt="continue")
        assert (
            resume_spec.env["AUTOSKILLIT_AGENT_BACKEND__BACKEND"]
            == resume_spec.env["AUTOSKILLIT_AGENT_BACKEND"]
        ), f"{name}: nested and flat AGENT_BACKEND values differ in build_resume_cmd"
