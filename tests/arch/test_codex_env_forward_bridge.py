"""Cross-layer bridge: CODEX_MCP_ENV_FORWARD_VARS ↔ cmd builders ↔ capabilities."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.execution.backends._plugin_binding import plugin_binding

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
_LONG_LIVED_CAPTURE_AUTHORITY_FILES = (
    _SRC_ROOT / "pipeline" / "context.py",
    _SRC_ROOT / "server" / "_factory.py",
    _SRC_ROOT / "server" / "tools" / "tools_execution.py",
    _SRC_ROOT / "server" / "tools" / "tools_fleet_dispatch" / "_handlers.py",
    _SRC_ROOT / "fleet" / "_api.py",
)
_MODE_ENV_NAMES = frozenset(
    {
        "NATIVE_SHELL_CAPTURE_MODE_ENV_VAR",
        "AUTOSKILLIT_NATIVE_SHELL_CAPTURE_MODE",
    }
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOSKILLIT_CAMPAIGN_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_KITCHEN_SESSION_ID", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND__BACKEND", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_MCP_CLIENT_BACKEND", raising=False)


def test_codex_forward_vars_subset_of_codex_cmd_env() -> None:
    """Every non-launch-scoped forward var is injected by Codex command builders."""
    from autoskillit.core import CODEX_MCP_ENV_FORWARD_VARS, LAUNCH_ID_ENV_VAR, OutputFormat
    from autoskillit.execution.backends.codex import CodexBackend

    backend = CodexBackend()
    launch_id = "0123456789abcdef"
    skill_spec = backend.build_skill_session_cmd(
        skill_command="/test-skill",
        cwd="/work",
        completion_marker="%%DONE%%",
        model=None,
        plugin_binding=None,
        output_format=OutputFormat.JSON,
        provider_extras={LAUNCH_ID_ENV_VAR: launch_id},
    )
    with plugin_binding(Path("/projected-plugin")) as binding:
        food_truck_spec = backend.build_food_truck_cmd(
            orchestrator_prompt="dispatch",
            plugin_binding=binding,
            cwd="/work",
            completion_marker="%%DONE%%",
            env_extras={LAUNCH_ID_ENV_VAR: launch_id},
        )
    assert skill_spec.env[LAUNCH_ID_ENV_VAR] == launch_id
    assert food_truck_spec.env[LAUNCH_ID_ENV_VAR] == launch_id
    for var in sorted(CODEX_MCP_ENV_FORWARD_VARS - {LAUNCH_ID_ENV_VAR}):
        assert var in skill_spec.env, (
            f"{var} in CODEX_MCP_ENV_FORWARD_VARS but missing from build_skill_session_cmd env"
        )
        assert var in food_truck_spec.env, (
            f"{var} in CODEX_MCP_ENV_FORWARD_VARS but missing from build_food_truck_cmd env"
        )


def test_always_injected_mcp_server_vars_forwarded_by_codex() -> None:
    """Vars bypassing PRIVATE_ENV_VARS must be in CODEX_MCP_ENV_FORWARD_VARS."""
    from autoskillit.core import (
        AUTOSKILLIT_PRIVATE_ENV_VARS,
        CODEX_MCP_ENV_FORWARD_VARS,
        ORCHESTRATOR_SESSION_REQUIRED_ENV,
        SKILL_SESSION_REQUIRED_ENV,
    )

    all_required = SKILL_SESSION_REQUIRED_ENV | ORCHESTRATOR_SESSION_REQUIRED_ENV
    bypass_vars = all_required - AUTOSKILLIT_PRIVATE_ENV_VARS - {"MCP_CONNECTION_NONBLOCKING"}
    missing = bypass_vars - CODEX_MCP_ENV_FORWARD_VARS
    assert not missing, (
        f"Vars bypassing PRIVATE_ENV_VARS but missing from CODEX_MCP_ENV_FORWARD_VARS: {missing}. "
        f"The Codex MCP server won't receive these through config.toml."
    )


def test_codex_capabilities_forward_vars_match_constant() -> None:
    """CodexBackend.capabilities.mcp_env_forward_vars must equal CODEX_MCP_ENV_FORWARD_VARS."""
    from autoskillit.core import CODEX_MCP_ENV_FORWARD_VARS
    from autoskillit.execution.backends.codex import CodexBackend

    assert CodexBackend().capabilities.mcp_env_forward_vars == CODEX_MCP_ENV_FORWARD_VARS


def _is_mode_env_key(node: ast.expr) -> bool:
    return (isinstance(node, ast.Name) and node.id in _MODE_ENV_NAMES) or (
        isinstance(node, ast.Constant) and node.value in _MODE_ENV_NAMES
    )


def _ambient_mode_reads(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    reads: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args and _is_mode_env_key(node.args[0]):
            func = node.func
            is_environ_method = (
                isinstance(func, ast.Attribute)
                and func.attr in {"get", "pop", "setdefault"}
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "environ"
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "os"
            )
            is_getenv = (
                isinstance(func, ast.Attribute)
                and func.attr == "getenv"
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
            )
            if is_environ_method or is_getenv:
                reads.append(node.lineno)
        elif (
            isinstance(node, ast.Subscript)
            and _is_mode_env_key(node.slice)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "environ"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "os"
        ):
            reads.append(node.lineno)
    return reads


def test_long_lived_launch_boundaries_never_read_ambient_capture_mode() -> None:
    violations = {
        str(path.relative_to(_SRC_ROOT)): lines
        for path in _LONG_LIVED_CAPTURE_AUTHORITY_FILES
        if (lines := _ambient_mode_reads(path))
    }

    assert not violations, (
        "Long-lived launch boundaries must receive native shell capture mode as a typed "
        f"per-launch argument, not ambient authority: {violations}"
    )
