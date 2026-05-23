"""Tests for the token_summary_appender PostToolUse hook — script existence and source quality.

Behavioral tests (early exit, PR editing, fail-open, order_id isolation, efficiency table)
and internal function unit tests (_canonical, _humanize, _format_table, _unwrap_mcp_response)
live in tests/infra/test_token_summary_core.py, tests/infra/test_token_summary_filters.py,
and tests/infra/test_token_summary_v1_compat.py.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# TSA-1: hook script exists on disk
# ---------------------------------------------------------------------------


def test_tsa1_token_summary_appender_script_exists() -> None:
    """token_summary_hook.py must exist in hooks/ on disk."""
    from autoskillit.core.paths import pkg_root

    assert (pkg_root() / "hooks" / "token_summary_hook.py").exists()


def test_tsa_rest_api_no_gh_pr_commands() -> None:
    """Hook source must not contain 'gh pr edit' or 'gh pr view' subprocess calls.

    REQ-TEST-001: verifies both read and write operations use gh api (REST).
    """
    from autoskillit.core.paths import pkg_root

    source = (pkg_root() / "hooks" / "token_summary_hook.py").read_text(encoding="utf-8")
    assert "gh pr edit" not in source, (
        "gh pr edit found in hook — must be replaced with "
        "gh api repos/.../pulls/{N} --method PATCH --field body=..."
    )
    assert "gh pr view" not in source, (
        "gh pr view found in hook — must be replaced with gh api repos/.../pulls/{N} --jq '.body'"
    )
