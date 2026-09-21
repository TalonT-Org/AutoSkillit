"""Architectural invariant: MCP capabilities cover private builder environment values."""

from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.codex import codex_skill_add_dirs

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SKILL_SESSION_ADD_DIRS = codex_skill_add_dirs("/repo", skill_name="investigate")
_CODEX_TEST_HOME = Path("/repo/codex-home")


def _codex_home_kwargs(backend: Any, parameter: str) -> dict[str, object]:
    if backend.name != "codex":
        return {}
    value: object = str(_CODEX_TEST_HOME) if parameter == "session_home" else _CODEX_TEST_HOME
    return {parameter: value}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND__BACKEND", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_MCP_CLIENT_BACKEND", raising=False)


@pytest.mark.parametrize(
    "builder_kind",
    ("skill_session", "food_truck", "headless", "resume", "interactive"),
)
def test_mcp_config_capability_covers_private_builder_env(builder_kind: str) -> None:
    """MCP config capabilities must cover private values emitted by each builder."""
    from autoskillit.core import (
        AUTOSKILLIT_PRIVATE_ENV_VARS,
        CODEX_MCP_ENV_SERVER_EXCLUDED_VARS,
    )
    from autoskillit.execution.backends import BACKEND_REGISTRY

    for name, cls in BACKEND_REGISTRY.items():
        backend = cls()
        if not backend.capabilities.mcp_config_capable:
            continue
        if builder_kind == "skill_session":
            spec = backend.build_skill_session_cmd(
                "/autoskillit:investigate",
                "/repo",
                completion_marker="DONE",
                add_dirs=_SKILL_SESSION_ADD_DIRS,
            )
        elif builder_kind == "food_truck":
            from tests.execution.backends._plugin_binding import plugin_binding

            with plugin_binding(Path("/plugins")) as binding:
                spec = backend.build_food_truck_cmd(
                    orchestrator_prompt="test prompt",
                    plugin_binding=binding,
                    cwd="/repo",
                    completion_marker="DONE",
                    managed_skill_catalog=_SKILL_SESSION_ADD_DIRS[0],
                )
        elif builder_kind == "headless":
            if not hasattr(backend, "build_headless_cmd"):
                continue
            spec = backend.build_headless_cmd(
                prompt="test", **_codex_home_kwargs(backend, "generated_home")
            )
        elif builder_kind == "resume":
            if not backend.capabilities.session_resume_capable:
                continue
            spec = backend.build_resume_cmd(
                resume_session_id="test-session",
                prompt="continue",
                **_codex_home_kwargs(backend, "session_home"),
            )
        elif builder_kind == "interactive":
            spec = backend.build_interactive_cmd(**_codex_home_kwargs(backend, "generated_home"))
        else:
            raise AssertionError(f"Unknown builder kind: {builder_kind}")

        undeclared_private_vars = (
            set(spec.env) & AUTOSKILLIT_PRIVATE_ENV_VARS
        ) - backend.capabilities.mcp_env_forward_vars
        assert undeclared_private_vars <= CODEX_MCP_ENV_SERVER_EXCLUDED_VARS, (
            f"{name} {builder_kind} emits private environment values absent from "
            f"mcp_env_forward_vars: {sorted(undeclared_private_vars)}"
        )
