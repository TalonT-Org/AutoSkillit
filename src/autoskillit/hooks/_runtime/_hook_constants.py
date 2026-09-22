"""Canonical stdlib-only authority for hook-script constants.

Importable in two contexts:
1. Standalone subprocess hook scripts — `from _hook_constants import …` after
   the `sys.path.insert(0, _HOOKS_DIR)` bootstrap.
2. Inside the autoskillit package — `from autoskillit.hooks._runtime._hook_constants import …`.

Both contexts resolve to the same module object, so the constants are a
single source of truth.
"""

from __future__ import annotations

from typing import Final

CODEX_AUTO_COMPACTION_DENIED_REASON: Final[str] = "autoskillit_auto_compaction_denied"
MANAGED_JOIN_PARENT_ID_ENV_VAR: Final[str] = "AUTOSKILLIT_MANAGED_JOIN_PARENT_ID"

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

RISKY_GH_SUBCOMMANDS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("run", "download"),
        ("release", "download"),
        ("pr", "create"),
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

# ── Managed Codex parent-route direct-tool surface ───────────────────────────
# Single canonical ordered allow-list referenced by every guard that branches
# on managed parent routes and by generated Codex homes.

MANAGED_PARENT_ALLOWED_TOOLS: Final[tuple[str, ...]] = (
    "run_fixed_batch",
    "read_fixed_batch_result",
)
MANAGED_PARENT_ALLOWED_TOOL_SET: Final[frozenset[str]] = frozenset(MANAGED_PARENT_ALLOWED_TOOLS)
