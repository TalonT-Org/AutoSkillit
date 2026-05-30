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
