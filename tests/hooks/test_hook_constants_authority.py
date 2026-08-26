"""Tests for the shared stdlib-only authority module `autoskillit.hooks._hook_constants`.

These tests pin down the canonical values of the constants that are
shared between the registry implementation (`autoskillit.hook_registry`)
and the three guard scripts that import from this module. The module
must be stdlib-only: zero `autoskillit.*` imports allowed (test_module_is_stdlib_only).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.hooks import _hook_constants
from autoskillit.hooks._hook_constants import (
    DENY_REASON_BY_GUARD,
    DENY_TRIGGER_BY_GUARD,
    EXEMPT_SESSION_TYPES_BY_GUARD,
    EXEMPT_SKILLS_BY_GUARD,
    RISKY_GH_SUBCOMMANDS,
    RISKY_GIT_OPERATIONS,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


def test_risky_git_operations_matches_literal() -> None:
    """RISKY_GIT_OPERATIONS must equal the canonical 9-tuple set the guards duplicated."""
    assert RISKY_GIT_OPERATIONS == frozenset(
        {
            ("commit", "--amend"),
            ("push", "--force"),
            ("push", "-f"),
            ("push", "--force-with-lease"),
            ("reset", "--hard"),
            ("clean", "-f"),
            ("clean", "-fd"),
            ("checkout", "."),
            ("checkout", "--", "."),
        }
    )


def test_risky_gh_subcommands_matches_literal() -> None:
    """RISKY_GH_SUBCOMMANDS must equal the canonical 3-tuple set."""
    assert RISKY_GH_SUBCOMMANDS == frozenset(
        {
            ("run", "download"),
            ("release", "download"),
            ("pr", "create"),
        }
    )


def test_exempt_skills_by_guard_covers_all_three_guards() -> None:
    """All three guards must have entries in EXEMPT_SKILLS_BY_GUARD with the per-guard values."""
    assert set(EXEMPT_SKILLS_BY_GUARD.keys()) == {
        "git_ops_guard",
        "test_runner_guard",
        "pr_create_guard",
    }
    assert EXEMPT_SKILLS_BY_GUARD["git_ops_guard"] == frozenset()
    assert EXEMPT_SKILLS_BY_GUARD["test_runner_guard"] == frozenset({"implement-experiment"})
    assert EXEMPT_SKILLS_BY_GUARD["pr_create_guard"] == frozenset(
        {
            "compose-pr",
            "compose-research-pr",
            "open-integration-pr",
            "promote-to-main",
            "pipeline-summary",
        }
    )


def test_exempt_session_types_by_guard_contains_only_pr_create_guard() -> None:
    """EXEMPT_SESSION_TYPES_BY_GUARD must contain only `pr_create_guard`.

    The registry-level field for `git_ops_guard` MUST stay empty (the orchestrator
    bypass is script-local, enforced in the guard after the destructive-op match);
    `test_runner_guard` has no session-type exemption.
    """
    assert set(EXEMPT_SESSION_TYPES_BY_GUARD.keys()) == {"pr_create_guard"}
    assert EXEMPT_SESSION_TYPES_BY_GUARD["pr_create_guard"] == frozenset({"orchestrator"})


def test_deny_trigger_by_guard_strings() -> None:
    """DENY_TRIGGER_BY_GUARD must contain the exact deny-trigger strings."""
    assert DENY_TRIGGER_BY_GUARD == {
        "git_ops_guard": "Destructive git operation blocked in headless session",
        "test_runner_guard": "Direct pytest invocation is prohibited",
        "pr_create_guard": "PR creation via run_cmd is prohibited",
    }


def test_deny_reason_by_guard_strings() -> None:
    """DENY_REASON_BY_GUARD must contain the exact deny-reason templates."""
    assert set(DENY_REASON_BY_GUARD.keys()) == {"git_ops_guard", "pr_create_guard"}
    assert DENY_REASON_BY_GUARD["git_ops_guard"] == (
        "Destructive git operation '{op}' is blocked in headless skill sessions. "
        "Create a new commit instead of amending, and avoid force-push, reset --hard, "
        "clean -f, or checkout . in automated workflows."
    )
    assert DENY_REASON_BY_GUARD["pr_create_guard"] == (
        "PR creation via run_cmd is prohibited during recipe execution. "
        "Use the prepare_pr → compose_pr pipeline instead. Direct gh pr create "
        "bypasses mandatory arch-lens, annotation, and review steps."
    )


def test_module_is_stdlib_only() -> None:
    """The module must contain zero ``autoskillit`` imports (stdlib-only boundary).

    The standalone guard scripts import from this module under the existing
    ``_HOOKS_DIR`` sys.path bootstrap; any ``autoskillit`` import (bare or
    package-qualified) would create a circular dependency through the
    package context.
    """
    source_path = Path(_hook_constants.__file__)
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".", 1)[0]
                assert top != "autoskillit", (
                    f"_hook_constants imports autoskillit module {alias.name!r}; "
                    "the module must remain stdlib-only."
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                top = node.module.split(".", 1)[0]
                assert top != "autoskillit", (
                    f"_hook_constants imports autoskillit module {node.module!r}; "
                    "the module must remain stdlib-only."
                )
