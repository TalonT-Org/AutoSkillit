# tools/

MCP `@mcp.tool()` handlers registered on import.

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
- `_commit_outcome.py` — Support module: Durable outcome accounting for commit_files responses.
- `_ordering_telemetry.py` — Support module: Ordering-violation detection over pipeline session step records.
- `_overlay_state.py` — Support module: Locked, validated access to the kitchen session overlay.
- `_pipeline_deps.py` — Support module: Curated Phase A dependencies from the finalized recipe routing graph.
- `_preflight.py` — Support module: Dispatch-feasibility preflight — shared by open_kitchen and dispatch_food_truck.
- `_pre_commit_failure.py` — Support module: Pre-commit failure classification helpers for commit_files.
- `_pre_commit_transaction.py` — Support module: Pre-commit execution, re-stage, and retry transaction for commit_files.
- `_serve_helpers.py` — Support module: Unified serve-pipeline helpers.
- `_self_revert.py` — Support module: Commit base validation and advisory self-revert scan response handling.
- `_type_coercion.py` — Support module: Override value coercion and type-validation gate for typed recipe ingredients.
- `_types.py` — Support module: Server tool response TypedDicts — typed contracts for MCP tool JSON responses.
- `tools_agents.py` — Registers agent listing, exploration, and agent-resource lookup.
- `tools_audit_artifacts.py` — Registers typed, server-owned audit semantic, standalone, and disposition producers.
- `tools_ci.py` — Registers `set_commit_status` and `check_repo_merge_state`.
- `tools_ci_merge_queue.py` — Registers `toggle_auto_merge`, `enqueue_pr`, and `wait_for_merge_queue`.
- `tools_ci_watch.py` — Registers `wait_for_ci` and `get_ci_status`.
- `tools_clone.py` — Registers clone, removal, push, status, cleanup, and bootstrap operations.
- `tools_config.py` — Registers session-scoped configuration operations.
- `tools_evidence_reader.py` — Registers the fail-closed behavioral evidence-reader surface.
- `tools_exploration.py` — Registers narrow, read-only broker tools for specialized repository explorers.
- `tools_git.py` — Registers merge, branch-creation, and fix-classification handlers.
- `tools_github.py` — Registers issue lookup and bug-report handlers.
- `tools_issue_labels.py` — Registers issue-label management operations.
- `tools_pr_ops.py` — Registers PR review reads, receipt verification, authoritative writes, and issue closure.
- `tools_recipe.py` — Registers recipe loading, listing, validation, and migration handlers.
- `tools_status.py` — Registers kitchen, pipeline, token, timing, and database status handlers.
- `tools_workspace.py` — Registers worktree testing, commits, and workspace-reset handlers.
