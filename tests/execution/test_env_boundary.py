"""Contract: every fleet-injected env var must appear in at least one filter list."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_FLEET_INJECTED_VARS: frozenset[str] = frozenset(
    {
        "AUTOSKILLIT_CAMPAIGN_ID",
        "AUTOSKILLIT_DISPATCH_ID",
        "AUTOSKILLIT_SESSION_DEADLINE",
    }
)

_CLAUDE_CODE_PASSTHROUGH_VARS: frozenset[str] = frozenset(
    {
        "CLAUDE_CODE_EXECPATH",  # intentional: binary discovery (_version_snapshot.py)
    }
)


def test_codex_mcp_env_forward_vars_subset_of_private() -> None:
    """Every var in CODEX_MCP_ENV_FORWARD_VARS must be in AUTOSKILLIT_PRIVATE_ENV_VARS."""
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS
    from autoskillit.core.types._type_constants_env import CODEX_MCP_ENV_FORWARD_VARS

    uncovered = CODEX_MCP_ENV_FORWARD_VARS - AUTOSKILLIT_PRIVATE_ENV_VARS
    assert not uncovered, (
        f"CODEX_MCP_ENV_FORWARD_VARS not in PRIVATE_ENV_VARS: {uncovered}. "
        f"All forwarded vars must be private to prevent uncontrolled propagation."
    )


def test_codex_forward_vars_cover_server_consumed() -> None:
    """CODEX_MCP_ENV_FORWARD_VARS must cover every server-consumed env var."""
    from autoskillit.core import (
        AGENT_BACKEND_DYNACONF_ENV_VAR,
        CODEX_MCP_ENV_FORWARD_VARS,
        FOOD_TRUCK_TOOL_TAGS_ENV_VAR,
        HEADLESS_AUTO_GATE_ENV_VAR,
        HEADLESS_ENV_VAR,
        MCP_CLIENT_BACKEND_ENV_VAR,
        SESSION_TYPE_ENV_VAR,
    )

    server_consumed_forward_vars: frozenset[str] = frozenset(
        {
            HEADLESS_ENV_VAR,
            HEADLESS_AUTO_GATE_ENV_VAR,
            MCP_CLIENT_BACKEND_ENV_VAR,
            SESSION_TYPE_ENV_VAR,
            FOOD_TRUCK_TOOL_TAGS_ENV_VAR,
            AGENT_BACKEND_DYNACONF_ENV_VAR,
        }
    )
    missing = server_consumed_forward_vars - CODEX_MCP_ENV_FORWARD_VARS
    assert not missing, (
        f"Server-consumed vars missing from CODEX_MCP_ENV_FORWARD_VARS: {missing}. "
        f"The MCP server reads these via os.environ.get() but Codex config.toml "
        f"won't forward them without an entry in CODEX_MCP_ENV_FORWARD_VARS."
    )


def test_ensure_codex_mcp_registered_env_vars_match_canonical_set(tmp_path) -> None:
    """ensure_codex_mcp_registered() must write exactly CODEX_MCP_ENV_FORWARD_VARS."""
    import tomllib

    from autoskillit.core import CODEX_MCP_ENV_FORWARD_VARS
    from autoskillit.execution.backends._codex_config import ensure_codex_mcp_registered

    config_path = tmp_path / "config.toml"
    ensure_codex_mcp_registered(config_path=config_path, headless_auto_gate=True)
    data = tomllib.loads(config_path.read_bytes().decode())
    env_vars = data["mcp_servers"]["autoskillit"]["env_vars"]
    assert set(env_vars) == set(CODEX_MCP_ENV_FORWARD_VARS), (
        f"Written env_vars {sorted(env_vars)} != canonical "
        f"CODEX_MCP_ENV_FORWARD_VARS {sorted(CODEX_MCP_ENV_FORWARD_VARS)}"
    )


def test_ensure_codex_mcp_registered_auto_gate_false_excludes_only_auto_gate(tmp_path) -> None:
    """headless_auto_gate=False must exclude only HEADLESS_AUTO_GATE_ENV_VAR."""
    import tomllib

    from autoskillit.core import CODEX_MCP_ENV_FORWARD_VARS, HEADLESS_AUTO_GATE_ENV_VAR
    from autoskillit.execution.backends._codex_config import ensure_codex_mcp_registered

    config_path = tmp_path / "config.toml"
    ensure_codex_mcp_registered(config_path=config_path, headless_auto_gate=False)
    data = tomllib.loads(config_path.read_bytes().decode())
    env_vars = data["mcp_servers"]["autoskillit"]["env_vars"]
    expected = CODEX_MCP_ENV_FORWARD_VARS - {HEADLESS_AUTO_GATE_ENV_VAR}
    assert set(env_vars) == set(expected), (
        f"With headless_auto_gate=False, written env_vars {sorted(env_vars)} != "
        f"expected {sorted(expected)}"
    )


def test_fleet_injected_vars_covered_by_filter_lists() -> None:
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS
    from autoskillit.execution.commands import _HEADLESS_EXCLUSIVE_VARS

    combined = AUTOSKILLIT_PRIVATE_ENV_VARS | _HEADLESS_EXCLUSIVE_VARS
    uncovered = _FLEET_INJECTED_VARS - combined
    assert not uncovered, (
        f"Fleet-injected vars missing from both filter lists: {uncovered}. "
        f"Add each to AUTOSKILLIT_PRIVATE_ENV_VARS or _HEADLESS_EXCLUSIVE_VARS."
    )


def test_no_unrecognized_claude_code_vars_pass_through() -> None:
    """Any known CLAUDE_CODE_* var must be filtered or documented as intentional passthrough."""
    from autoskillit.core._claude_env import IDE_ENV_DENYLIST, IDE_ENV_PREFIX_DENYLIST
    from autoskillit.execution.commands import _HEADLESS_EXCLUSIVE_VARS

    known_vars = [
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY",
        "CLAUDE_CODE_SSE_PORT",
        "CLAUDE_CODE_IDE_THEME",
        "CLAUDE_CODE_EXECPATH",
    ]

    for var in known_vars:
        in_exclusive = var in _HEADLESS_EXCLUSIVE_VARS
        in_denylist = var in IDE_ENV_DENYLIST
        in_prefix = any(var.startswith(p) for p in IDE_ENV_PREFIX_DENYLIST)
        in_passthrough = var in _CLAUDE_CODE_PASSTHROUGH_VARS

        assert in_exclusive or in_denylist or in_prefix or in_passthrough, (
            f"{var} is a CLAUDE_CODE_* var that passes through all env filters. "
            f"Add it to _HEADLESS_EXCLUSIVE_VARS, IDE_ENV_DENYLIST, or "
            f"IDE_ENV_PREFIX_DENYLIST — or to _CLAUDE_CODE_PASSTHROUGH_VARS "
            f"if passthrough is intentional."
        )


def test_codex_mcp_env_forward_vars_parity_with_config_toml(tmp_path) -> None:
    """Every CODEX_MCP_ENV_FORWARD_VARS member must appear in config.toml env_vars."""
    import tomllib

    from autoskillit.core.types._type_constants_env import CODEX_MCP_ENV_FORWARD_VARS
    from autoskillit.execution.backends._codex_config import ensure_codex_mcp_registered

    config_path = tmp_path / "config.toml"
    ensure_codex_mcp_registered(config_path=config_path, headless_auto_gate=True)
    data = tomllib.loads(config_path.read_bytes().decode())
    env_vars = frozenset(data["mcp_servers"]["autoskillit"]["env_vars"])
    missing = CODEX_MCP_ENV_FORWARD_VARS - env_vars
    assert not missing, (
        f"CODEX_MCP_ENV_FORWARD_VARS members missing from config.toml env_vars: {missing}"
    )


def test_ensure_codex_mcp_registered_includes_mcp_client_backend(tmp_path) -> None:
    """ensure_codex_mcp_registered must write MCP_CLIENT_BACKEND_ENV_VAR to env_vars."""
    import tomllib

    from autoskillit.core.types._type_constants_env import MCP_CLIENT_BACKEND_ENV_VAR
    from autoskillit.execution.backends._codex_config import ensure_codex_mcp_registered

    config_path = tmp_path / "config.toml"
    ensure_codex_mcp_registered(config_path=config_path)
    data = tomllib.loads(config_path.read_bytes().decode())
    env_vars = data["mcp_servers"]["autoskillit"]["env_vars"]
    assert MCP_CLIENT_BACKEND_ENV_VAR in env_vars, (
        f"MCP_CLIENT_BACKEND_ENV_VAR ({MCP_CLIENT_BACKEND_ENV_VAR}) must be present in "
        f"Codex MCP env_vars so the server knows the client backend at startup. Got: {env_vars}"
    )


def test_unknown_claude_code_var_is_caught() -> None:
    """Verify the filter framework catches unclassified CLAUDE_CODE_* vars."""
    from autoskillit.core._claude_env import IDE_ENV_DENYLIST, IDE_ENV_PREFIX_DENYLIST
    from autoskillit.execution.commands import _HEADLESS_EXCLUSIVE_VARS

    var = "CLAUDE_CODE_UNKNOWN_FUTURE_VAR"
    covered = (
        var in _HEADLESS_EXCLUSIVE_VARS
        or var in IDE_ENV_DENYLIST
        or any(var.startswith(p) for p in IDE_ENV_PREFIX_DENYLIST)
        or var in _CLAUDE_CODE_PASSTHROUGH_VARS
    )
    assert not covered, (
        f"{var} should NOT be covered — the guard test must catch unclassified vars"
    )


def test_orchestrator_session_required_env_hygiene_coverage() -> None:
    """Every ORCHESTRATOR_SESSION_REQUIRED_ENV var is accounted for in the env hygiene chain."""
    from autoskillit.core import (
        AUTOSKILLIT_PRIVATE_ENV_VARS,
        CODEX_MCP_ENV_FORWARD_VARS,
        ORCHESTRATOR_SESSION_REQUIRED_ENV,
    )
    from autoskillit.execution.commands import _HEADLESS_EXCLUSIVE_VARS

    always_injected = {
        "MCP_CONNECTION_NONBLOCKING",
    }
    allowed = AUTOSKILLIT_PRIVATE_ENV_VARS | _HEADLESS_EXCLUSIVE_VARS | always_injected
    uncovered = ORCHESTRATOR_SESSION_REQUIRED_ENV - allowed
    assert not uncovered, (
        f"Orchestrator required vars not in env hygiene chain: {uncovered}. "
        "Add to AUTOSKILLIT_PRIVATE_ENV_VARS, _HEADLESS_EXCLUSIVE_VARS, or inject via extras."
    )

    mcp_forward_required = always_injected - {"MCP_CONNECTION_NONBLOCKING"}
    missing_forward = mcp_forward_required - CODEX_MCP_ENV_FORWARD_VARS
    assert not missing_forward, (
        f"always_injected vars not in CODEX_MCP_ENV_FORWARD_VARS: {missing_forward}. "
        f"The Codex MCP server won't receive these vars through config.toml."
    )


def test_skill_session_required_env_hygiene_coverage() -> None:
    """Every SKILL_SESSION_REQUIRED_ENV var is accounted for in the env hygiene chain."""
    from autoskillit.core import (
        AUTOSKILLIT_PRIVATE_ENV_VARS,
        CODEX_MCP_ENV_FORWARD_VARS,
        SKILL_SESSION_REQUIRED_ENV,
    )
    from autoskillit.execution.commands import _HEADLESS_EXCLUSIVE_VARS

    always_injected = {
        "MCP_CONNECTION_NONBLOCKING",
    }
    allowed = AUTOSKILLIT_PRIVATE_ENV_VARS | _HEADLESS_EXCLUSIVE_VARS | always_injected
    uncovered = SKILL_SESSION_REQUIRED_ENV - allowed
    assert not uncovered, (
        f"Skill required vars not in env hygiene chain: {uncovered}. "
        "Add to AUTOSKILLIT_PRIVATE_ENV_VARS, _HEADLESS_EXCLUSIVE_VARS, or inject via extras."
    )

    mcp_forward_required = always_injected - {"MCP_CONNECTION_NONBLOCKING"}
    missing_forward = mcp_forward_required - CODEX_MCP_ENV_FORWARD_VARS
    assert not missing_forward, (
        f"always_injected vars not in CODEX_MCP_ENV_FORWARD_VARS: {missing_forward}. "
        f"The Codex MCP server won't receive these vars through config.toml."
    )
