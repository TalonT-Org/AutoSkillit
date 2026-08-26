"""Canonical stdlib-only authority for hook-script constants.

This module is importable in two contexts:
1. Standalone subprocess hook scripts (e.g., hooks/guards/*.py) — imported as
   `from _hook_constants import …` after the existing `sys.path.insert(0, _HOOKS_DIR)`
   bootstrap. No autoskillit package context is required.
2. Inside the autoskillit package — imported as
   `from autoskillit.hooks._hook_constants import …`. Both contexts resolve to
   the same module object, so the constants are a single source of truth.

Adding a new constant here is the only sanctioned way to share a value between
the registry implementation (src/autoskillit/hook_registry/) and a guard script.
Do not duplicate literals across these boundaries; both sides import from this
module instead.
"""

from __future__ import annotations

from typing import Final

# ── Risky operations requiring PreToolUse guard coverage ──────────────────────

RISKY_GIT_OPERATIONS: Final[frozenset[tuple[str, ...]]] = frozenset(
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

RISKY_GH_SUBCOMMANDS: Final[frozenset[tuple[str, ...]]] = frozenset(
    {
        ("pr", "create"),
        ("pr", "merge"),
        ("release", "create"),
    }
)

# ── Per-guard exempt sets (skill names whose execution may bypass the guard) ──

EXEMPT_SKILLS_BY_GUARD: Final[dict[str, frozenset[str]]] = {
    "git_ops_guard": frozenset(),
    "test_runner_guard": frozenset({"implement-experiment"}),
    "pr_create_guard": frozenset(
        {
            "compose-pr",
            "compose-research-pr",
            "open-integration-pr",
            "promote-to-main",
            "pipeline-summary",
        }
    ),
}

# EXEMPT_SESSION_TYPES_BY_GUARD intentionally contains only `pr_create_guard`.
# `git_ops_guard`'s orchestrator bypass is script-local (enforced in the guard
# script after the destructive-op match) — the registry-level
# `HookDef.exempt_session_types` for `git_ops_guard` MUST stay empty to satisfy
# tests/infra/test_session_type_exemption_enforcement.py::
# test_git_ops_guard_orchestrator_exemption_is_phase_local. `test_runner_guard`
# has no session-type exemption at all.
EXEMPT_SESSION_TYPES_BY_GUARD: Final[dict[str, frozenset[str]]] = {
    "pr_create_guard": frozenset({"orchestrator"}),
}

# ── Public deny-payload metadata ──────────────────────────────────────────────

DENY_TRIGGER_BY_GUARD: Final[dict[str, str]] = {
    "git_ops_guard": "Destructive git operation blocked in headless session",
    "test_runner_guard": "Direct pytest invocation is prohibited",
    "pr_create_guard": "PR creation via run_cmd is prohibited",
}

DENY_REASON_BY_GUARD: Final[dict[str, str]] = {
    "git_ops_guard": (
        "Destructive git operation '{op}' is blocked in headless skill sessions. "
        "Create a new commit instead of amending, and avoid force-push, reset --hard, "
        "clean -f, or checkout . in automated workflows."
    ),
    "pr_create_guard": (
        "PR creation via run_cmd is prohibited during recipe execution. "
        "Use the prepare_pr → compose_pr pipeline instead. Direct gh pr create "
        "bypasses mandatory arch-lens, annotation, and review steps."
    ),
}
