"""Tests for AUTOSKILLIT_PRIVATE_ENV_VARS membership + Codex env-forwarding contracts."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_private_env_vars_includes_franchise_tier_vars() -> None:
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS

    expected = {
        "AUTOSKILLIT_SESSION_TYPE",
        "AUTOSKILLIT_CAMPAIGN_ID",
        "AUTOSKILLIT_KITCHEN_SESSION_ID",
        "AUTOSKILLIT_CAMPAIGN_STATE_PATH",
        "AUTOSKILLIT_PROJECT_DIR",
        "AUTOSKILLIT_FOOD_TRUCK_TOOL_TAGS",
    }
    assert expected <= AUTOSKILLIT_PRIVATE_ENV_VARS
    assert "AUTOSKILLIT_L3_TOOL_TAGS" not in AUTOSKILLIT_PRIVATE_ENV_VARS


def test_private_env_vars_includes_execution_control_vars() -> None:
    """Execution-control vars must not leak into Codex subprocess environments."""
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS

    expected = {
        "SCENARIO_STEP_NAME",
        "AUTOSKILLIT_ALLOWED_WRITE_PREFIX",
        "AUTOSKILLIT_ALLOWED_WRITE_PREFIXES",
        "MAX_MCP_OUTPUT_TOKENS",
    }
    assert expected <= AUTOSKILLIT_PRIVATE_ENV_VARS


def test_private_env_vars_include_native_shell_lineage_controls() -> None:
    from autoskillit.core import (
        AUTOSKILLIT_PRIVATE_ENV_VARS,
        CODEX_MCP_ENV_FORWARD_VARS,
        MANAGED_ATTEMPT_ID_ENV_VAR,
        MANAGED_LAUNCH_ID_ENV_VAR,
        MANAGED_LINEAGE_DIGEST_ENV_VAR,
        MANAGED_LINEAGE_REF_ENV_VAR,
        NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
    )

    protected = {
        NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
        MANAGED_LAUNCH_ID_ENV_VAR,
        MANAGED_ATTEMPT_ID_ENV_VAR,
        MANAGED_LINEAGE_DIGEST_ENV_VAR,
        MANAGED_LINEAGE_REF_ENV_VAR,
    }
    assert protected <= AUTOSKILLIT_PRIVATE_ENV_VARS
    assert protected.isdisjoint(CODEX_MCP_ENV_FORWARD_VARS)


def test_session_deadline_in_private_env_vars() -> None:
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS

    assert "AUTOSKILLIT_SESSION_DEADLINE" in AUTOSKILLIT_PRIVATE_ENV_VARS


def test_fleet_inspector_model_crosses_only_declared_child_boundaries() -> None:
    from autoskillit.core import (
        AUTOSKILLIT_PRIVATE_ENV_VARS,
        CODEX_MCP_ENV_FORWARD_VARS,
        FLEET_INSPECTOR_MODEL_ENV_VAR,
    )

    assert FLEET_INSPECTOR_MODEL_ENV_VAR == "AUTOSKILLIT_FLEET_INSPECTOR_MODEL"
    assert FLEET_INSPECTOR_MODEL_ENV_VAR in AUTOSKILLIT_PRIVATE_ENV_VARS
    assert FLEET_INSPECTOR_MODEL_ENV_VAR in CODEX_MCP_ENV_FORWARD_VARS


def test_codex_mcp_receives_launch_registry_join() -> None:
    from autoskillit.core import (
        AUTOSKILLIT_STATE_ROOT_ENV_VAR,
        CODEX_MCP_ENV_FORWARD_VARS,
        LAUNCH_ID_ENV_VAR,
    )

    assert {LAUNCH_ID_ENV_VAR, AUTOSKILLIT_STATE_ROOT_ENV_VAR} <= CODEX_MCP_ENV_FORWARD_VARS


def test_campaign_id_env_var_and_kitchen_session_id_env_var_exported_from_core() -> None:
    """CAMPAIGN_ID_ENV_VAR and KITCHEN_SESSION_ID_ENV_VAR are re-exported from autoskillit.core."""
    from autoskillit.core import CAMPAIGN_ID_ENV_VAR, KITCHEN_SESSION_ID_ENV_VAR

    assert CAMPAIGN_ID_ENV_VAR == "AUTOSKILLIT_CAMPAIGN_ID"
    assert KITCHEN_SESSION_ID_ENV_VAR == "AUTOSKILLIT_KITCHEN_SESSION_ID"


def test_provider_profile_in_private_env_vars() -> None:
    """PROVIDER_PROFILE_ENV_VAR is the canonical name for the provider-profile env var
    and must be a member of AUTOSKILLIT_PRIVATE_ENV_VARS."""
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS, PROVIDER_PROFILE_ENV_VAR

    assert PROVIDER_PROFILE_ENV_VAR == "AUTOSKILLIT_PROVIDER_PROFILE"
    assert PROVIDER_PROFILE_ENV_VAR in AUTOSKILLIT_PRIVATE_ENV_VARS


def test_scenario_step_name_in_private_env_vars() -> None:
    """SCENARIO_STEP_NAME must be in AUTOSKILLIT_PRIVATE_ENV_VARS."""
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS

    assert "SCENARIO_STEP_NAME" in AUTOSKILLIT_PRIVATE_ENV_VARS


def test_autoskillit_allowed_write_prefix_in_private_env_vars() -> None:
    """AUTOSKILLIT_ALLOWED_WRITE_PREFIX must be in AUTOSKILLIT_PRIVATE_ENV_VARS."""
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS

    assert "AUTOSKILLIT_ALLOWED_WRITE_PREFIX" in AUTOSKILLIT_PRIVATE_ENV_VARS


def test_autoskillit_allowed_write_prefixes_in_private_env_vars() -> None:
    """AUTOSKILLIT_ALLOWED_WRITE_PREFIXES must be in AUTOSKILLIT_PRIVATE_ENV_VARS."""
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS

    assert "AUTOSKILLIT_ALLOWED_WRITE_PREFIXES" in AUTOSKILLIT_PRIVATE_ENV_VARS


def test_max_mcp_output_tokens_in_private_env_vars() -> None:
    """MAX_MCP_OUTPUT_TOKENS must be in AUTOSKILLIT_PRIVATE_ENV_VARS."""
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS

    assert "MAX_MCP_OUTPUT_TOKENS" in AUTOSKILLIT_PRIVATE_ENV_VARS


# ---------------------------------------------------------------------------
# AUTOSKILLIT_CWD membership in AUTOSKILLIT_PRIVATE_ENV_VARS (T1)
# ---------------------------------------------------------------------------


def test_autoskillit_cwd_in_private_env_vars() -> None:
    """AUTOSKILLIT_CWD must be in AUTOSKILLIT_PRIVATE_ENV_VARS."""
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS

    assert "AUTOSKILLIT_CWD" in AUTOSKILLIT_PRIVATE_ENV_VARS


def test_autoskillit_write_guard_tool_names_in_private_env_vars() -> None:
    """AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES must be scrubbed from ambient env."""
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS

    assert "AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES" in AUTOSKILLIT_PRIVATE_ENV_VARS
