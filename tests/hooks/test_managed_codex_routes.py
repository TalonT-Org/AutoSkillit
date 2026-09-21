"""Direct unit coverage for managed Codex hook additions and route helpers."""

from __future__ import annotations

import pytest

from autoskillit.execution.backends._codex_hooks import (
    MANAGED_CODEX_INTERACTIVE_PARENT_GUARD_SET,
    MANAGED_CODEX_LEAF_GUARD_SET,
    MANAGED_CODEX_PARENT_GUARD_SET,
    MANAGED_CODEX_ROUTE_NAMES,
    _managed_route_hook_defs,
    managed_codex_guard_set,
    managed_codex_mcp_tools,
    managed_codex_route_digest,
    managed_codex_route_for_launch_context,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


def test_route_for_launch_context_interactive_returns_interactive_parent() -> None:
    assert managed_codex_route_for_launch_context("interactive") == "interactive-parent"


def test_route_for_launch_context_direct_returns_parent() -> None:
    assert managed_codex_route_for_launch_context("direct") == "parent"


def test_route_for_launch_context_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unsupported managed Codex launch context"):
        managed_codex_route_for_launch_context("headless")


def test_route_digest_is_sha256_and_stable() -> None:
    first = managed_codex_route_digest()
    second = managed_codex_route_digest()

    assert first == second
    assert len(first) == 64
    int(first, 16)


def test_route_digest_is_deterministic_across_calls() -> None:
    """Two consecutive digest calls produce identical output."""
    from autoskillit.execution.backends import _codex_hooks as module

    original = module.MANAGED_CODEX_ROUTE_NAMES
    first = managed_codex_route_digest()
    second = managed_codex_route_digest()
    assert first == second

    module.MANAGED_CODEX_ROUTE_NAMES = original + ("unused",)  # type: ignore[assignment]
    try:
        with pytest.raises(ValueError):
            managed_codex_route_digest()
    finally:
        module.MANAGED_CODEX_ROUTE_NAMES = original


def test_guard_set_interactive_parent_membership() -> None:
    assert (
        managed_codex_guard_set("interactive-parent") == MANAGED_CODEX_INTERACTIVE_PARENT_GUARD_SET
    )
    assert "join_stop_guard" in MANAGED_CODEX_INTERACTIVE_PARENT_GUARD_SET
    assert "join_followup_guard" in MANAGED_CODEX_INTERACTIVE_PARENT_GUARD_SET
    assert "background_exec_guard" in MANAGED_CODEX_INTERACTIVE_PARENT_GUARD_SET
    assert "skill_orchestration_guard" not in MANAGED_CODEX_INTERACTIVE_PARENT_GUARD_SET


def test_guard_set_parent_and_leaf_distinct() -> None:
    parent = managed_codex_guard_set("parent")
    leaf = managed_codex_guard_set("leaf")

    assert parent == MANAGED_CODEX_PARENT_GUARD_SET
    assert leaf == MANAGED_CODEX_LEAF_GUARD_SET
    assert parent != leaf


def test_guard_set_unknown_route_raises() -> None:
    with pytest.raises(ValueError, match="unsupported managed Codex route"):
        managed_codex_guard_set("unknown-route")  # type: ignore[arg-type]


def test_mcp_tools_parent_and_leaf_return_explicit_lists() -> None:
    assert managed_codex_mcp_tools("parent") is not None
    assert managed_codex_mcp_tools("leaf") is not None
    assert isinstance(managed_codex_mcp_tools("parent"), tuple)
    assert isinstance(managed_codex_mcp_tools("leaf"), tuple)


def test_mcp_tools_interactive_parent_returns_none() -> None:
    assert managed_codex_mcp_tools("interactive-parent") is None


def test_mcp_tools_unknown_route_raises() -> None:
    with pytest.raises(ValueError, match="unsupported managed Codex route"):
        managed_codex_mcp_tools("unknown-route")  # type: ignore[arg-type]


def test_route_names_contains_all_routes() -> None:
    assert MANAGED_CODEX_ROUTE_NAMES == ("parent", "leaf", "interactive-parent")


def test_managed_route_hook_defs_interactive_parent_drops_skill_orchestration_guard() -> None:
    hooks = _managed_route_hook_defs("interactive-parent")
    scripts = {script for hook in hooks for script in hook.scripts}

    assert "guards/skill_orchestration_guard.py" not in scripts
    assert "guards/join_stop_guard.py" in scripts
    assert "guards/join_followup_guard.py" in scripts
    assert "guards/background_exec_guard.py" in scripts
    for hook in hooks:
        assert hook.session_scope == "any"


def test_managed_route_hook_defs_parent_keeps_skill_orchestration_guard() -> None:
    hooks = _managed_route_hook_defs("parent")
    scripts = {script for hook in hooks for script in hook.scripts}

    assert "guards/skill_orchestration_guard.py" in scripts
    for hook in hooks:
        assert hook.session_scope == "headless_only"


def test_managed_route_hook_defs_leaf_excludes_join_hooks() -> None:
    hooks = _managed_route_hook_defs("leaf")
    scripts = {script for hook in hooks for script in hook.scripts}

    assert "guards/skill_orchestration_guard.py" in scripts
    assert "guards/join_stop_guard.py" not in scripts
    assert "guards/join_followup_guard.py" not in scripts


def test_managed_route_hook_defs_unknown_route_yields_minimal_hooks() -> None:
    """An unknown route is treated like ``leaf`` minus the skill_orchestration guard."""
    hooks = _managed_route_hook_defs("unknown-route")  # type: ignore[arg-type]
    scripts = {script for hook in hooks for script in hook.scripts}
    assert "guards/skill_orchestration_guard.py" in scripts
    assert "guards/join_stop_guard.py" not in scripts


def test_generate_codex_hooks_config_uses_interactive_scope_for_interactive_parent() -> None:
    """``interactive-parent`` route must select interactive-scope hook entries."""
    from autoskillit.execution.backends._codex_hooks import _managed_route_hook_defs

    hooks = _managed_route_hook_defs("interactive-parent")
    for hook in hooks:
        assert hook.session_scope == "any"
