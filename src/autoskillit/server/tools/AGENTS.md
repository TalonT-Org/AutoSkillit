# tools/

MCP `@mcp.tool()` handlers registered on import (21 tool modules).

The package initializer is docstring-only; importing tool modules performs registration.
`serve_recipe()` is the only legal caller of `load_and_validate` within `server/tools/`.

## Architecture Notes

Side-effect registration (same pattern as `recipe/rules/`). The `server/__init__.py` owns the `mcp` app object; tool modules import it from the server layer. All tools except the replay-journaled, effectful `open_kitchen` transition require `readOnlyHint: True` (see `server/AGENTS.md`).

`post_pr_review` is the sole headless PR-review publication authority. It validates exact
repository/head/iteration identity, delegates durable pacing and reconciliation to the
GitHub-review execution service, and returns the authoritative receipt identity that recipe
effect gates must verify. Its `readOnlyHint: True` preserves server-side parallel scheduling;
the private ledger provides the required mutation serialization.

## Multi-concern files

Files with no registration support their folder; files with several registrations group one related concern.

- `__init__.py` — Support module: Server tool modules — @mcp.tool() handlers registered on import.
- `_authority_feedback.py` — Support module: Shared authority-violation feedback helpers for open_kitchen, load_recipe, and lock_ingredients.
- `_auto_overrides.py` — Support module: Explicit per-step backend authority mapping for recipe execution.
- `_backend_compat.py` — Support module: Shared backend-compatibility setup for direct headless executor callers.
- `_cancellation_shield.py` — Support module: Cancellation shield decorator for MCP tool handlers.
- `_claim_helpers.py` — Support module: Shared claiming logic for claim_issue and claim_and_resolve_issue.
- `_ordering_telemetry.py` — Support module: Ordering-violation detection over pipeline session step records.
- `_overlay_state.py` — Support module: Locked, validated access to the kitchen session overlay.
- `_pipeline_deps.py` — Support module: Curated Phase A dependencies from the finalized recipe routing graph.
- `_preflight.py` — Support module: Dispatch-feasibility preflight — shared by open_kitchen and dispatch_food_truck.
- `_pre_commit_failure.py` — Support module: Pre-commit failure classification helpers for commit_files.
- `_serve_helpers.py` — Support module: Unified serve-pipeline helpers.
- `_type_coercion.py` — Support module: Override value coercion and type-validation gate for typed recipe ingredients.
- `_types.py` — Support module: Server tool response TypedDicts — typed contracts for MCP tool JSON responses.
- `tools_agents.py` — Groups 3 registrations: Agent listing, exploration, and agent-resource lookup.
- `tools_audit_artifacts.py` — Groups 3 registrations: Typed, server-owned audit semantic, standalone, and disposition producers.
- `tools_ci.py` — Groups 2 registrations: MCP tool handlers: set_commit_status (gated), check_repo_merge_state (gated).
- `tools_ci_merge_queue.py` — Groups 3 registrations: Merge queue MCP tools: toggle_auto_merge, enqueue_pr, wait_for_merge_queue.
- `tools_ci_watch.py` — Groups 2 registrations: CI watching MCP tools: wait_for_ci and get_ci_status.
- `tools_clone.py` — Groups 6 registrations: MCP tool handlers: clone_repo, remove_clone, push_to_remote, register_clone_status, batch_cleanup_clones, bootstrap_clone.
- `tools_config.py` — Groups 2 registrations: Session-scoped configuration MCP tools: configure_fleet, configure_order.
- `tools_evidence_reader.py` — Groups 3 registrations: Fail-closed tool surface for behavioral evidence readers.
- `tools_exploration.py` — Groups 4 registrations: Narrow, read-only broker tools for specialized repository explorers.
- `tools_git.py` — Groups 5 registrations: MCP tool handlers for merge, branch creation, and fix classification.
- `tools_github.py` — Groups 3 registrations: MCP tool handlers: fetch_github_issue, get_issue_title, report_bug.
- `tools_issue_labels.py` — Groups 2 registrations: MCP tool handlers: claim_issue, release_issue (GitHub label management).
- `tools_pr_ops.py` — Groups 3 registrations: MCP tool handlers: PR review reads, authoritative writes, and issue closure.
- `tools_recipe.py` — Groups 5 registrations: MCP tool handlers: load_recipe, list_recipes, validate_recipe, migrate_recipe.
- `tools_status.py` — Groups 8 registrations: MCP tool handlers: kitchen_status, get_pipeline_report, get_token_summary, get_timing_summary, read_db.
- `tools_workspace.py` — Groups 4 registrations: MCP tool handlers for worktree testing, commits, and workspace reset.
