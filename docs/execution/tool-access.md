# MCP Tool Access Control

AutoSkillit provides MCP tools across overlapping visibility surfaces that control which
session types can see each tool. Visibility determines addressability; each tool still enforces
its own authority contract.

## Principal Access Surfaces

```
┌─────────────────────────────────────────────────────────┐
│  FREE RANGE  (ordinarily visible)                       │
│  Kitchen transitions and session configuration          │
│  Ordinarily visible — no application gate               │
├─────────────────────────────────────────────────────────┤
│  HEADLESS-TAGGED                                        │
│  test/check, commit, issue reader, audit, review tools  │
│  Revealed in headless sessions via mcp.enable(headless) │
│  Some also carry kitchen; `post_pr_review` is headless-only │
├─────────────────────────────────────────────────────────┤
│  KITCHEN                                                 │
│  Derived from the gated set minus other session surfaces │
│  Hidden at startup; revealed when open_kitchen is called│
├─────────────────────────────────────────────────────────┤
│  EVIDENCE READER                                         │
│  Exact reader-only startup projection; never kitchen    │
└─────────────────────────────────────────────────────────┘
```

Fleet and fleet-dispatch tags expose session-specific tools and do not define an
additional authority tier. Only INSPECTION tools may carry both `kitchen` and
`fleet-dispatch`.

## Session Mode Access Matrix

| Session Mode | Free Range | Kitchen tools | Headless-tagged |
|---|---|---|---|
| `$ claude` (plugin, no kitchen) | ✓ | ✗ | ✗ |
| `$ claude` (after `/open-kitchen`) | ✓ | ✓ | ✗ |
| `$ autoskillit cook` | ✓ | ✓ (pre-revealed) | ✗ |
| `$ autoskillit order` | ✓ | ✓ (pre-opened) | ✗ |
| `run_skill` (headless) | ✓ | ✗ | ✓ (includes GitHub issue readers) |
| Evidence-reader child | ✗ | ✗ | ✗ (reader brokers only) |
| L2 food truck | ✓ | ✓ (pre-opened) | ✗ |
| L3 fleet | ✓ | fleet surface | ✗ |

Note: Disabled subsets further restrict visibility within the Kitchen tier — their tools
remain hidden even after `open_kitchen`. Disabling the `github` subset also hides the
issue readers in headless skill sessions.

The two authenticated evidence-reader broker tools belong to a separate private surface.
They are not kitchen, free-range, or fleet tools. A complete reader startup identity reveals
exactly those two tools; an absent identity follows ordinary startup, while a partial or malformed
identity fails closed.

Visibility is not authority. At the application and hook layers, `run_skill` is restricted
to exact L2 `ORCHESTRATOR` sessions. L3 `FLEET` sessions create L2 food trucks through
`dispatch_food_truck`; they retain `run_cmd` and `run_python` but cannot call `run_skill`.

For Codex, native `declare_join_batch` remains unavailable because the backend's
native coordination surface is wait-any rather than fixed-set fan-in. A managed
parent is admitted only with a server-issued direct-mode attestation and uses
`run_fixed_batch` instead. Its leaf sessions are separate, bound contexts with a
small direct-tool allow-list; they are not a delegated copy of the parent's
orchestration surface. Doctor reports project-specific configuration and
conformance observations. It does not act as an external attestation authority.

## Behavioral Evidence Readers

`delegate_evidence_reader` is a headless parent tool for writable L1 Codex skill sessions.
The first eligible role is the bundled `pr-source-reader`. Its `role_data` accepts exactly one
repository-relative `artifact_path` and a bounded `requested_fields` list; callers cannot override
the role definition, repository root, model, prompt, tools, transport, environment, catalog,
sandbox, approval policy, or authority binding.

The server resolves the trusted worktree and captures one stable artifact before launch. The
snapshot covers the current bytes, mode, repository identity, revision, and path-specific index
records, so tracked, dirty, staged, and untracked artifacts are handled without widening access to
the repository. The captured bytes remain immutable for paging. Broker receipts bind every issued
citation to that snapshot and record its byte and line location.

The reader runs as a separate top-level Codex process with a sterile home and working directory,
`read-only` sandbox, `never` approval policy, no repository mount, and command, delegation, web,
app, plugin, and permission-request surfaces disabled. Its complete private startup identity
selects only `read_authorized_artifact` and `get_authorized_artifact_page`; both brokers reopen and
authenticate the invocation authority on every call.

Completion is fail closed. AutoSkillit validates the bounded result and receipt citations,
recaptures the artifact to reject stale evidence, terminates the process tree, revokes the reader
authority, and removes its generated state. Cleanup, recapture, binding, schema, timeout, and
cancellation failures cannot be reported as successful evidence.

The conformance record attests generated configuration, the selected catalog projection, the
exact configured AutoSkillit MCP allowlist, and observed calls and behavioral canaries. Codex does
not expose a complete offered built-in tool inventory, so this evidence demonstrates the supported
behavioral contract; it does not claim exhaustive observation of every built-in tool Codex might
offer.

## FastMCP Tag Glossary

| Tag | Abbrev | Meaning |
|-----|--------|---------|
| `autoskillit` | AS | Identifies AutoSkillit tools; present on every tool. |
| `kitchen` | K | Hidden at startup; revealed by kitchen opening or session pre-reveal. |
| `headless` | HL | Revealed in headless skill sessions. Most members also carry `kitchen`; `post_pr_review` is headless-only. |
| `kitchen-core` | KC | Core kitchen pack visible in admitted sessions. |
| `fleet` | FL | Revealed to fleet sessions. |
| `fleet-dispatch` | FD | Additionally revealed in fleet dispatch mode. |
| `evidence-reader` | ER | Authenticated artifact brokers enabled only by a verified reader binding. |
| `exploration` | EX | Capability-bound exploration brokers. |
| `github` | GH | GitHub tools; can be disabled as a subset. |
| `ci` | CI | CI and merge-queue tools; can be disabled as a subset. |
| `clone` | CL | Clone operations; can be disabled as a subset. |
| `telemetry` | TL | Token, timing, and quota tools; can be disabled as a subset. |

Only INSPECTION tools may carry both `kitchen` and `fleet-dispatch`; their kitchen
tag makes them available in ordinary opened kitchens, while `fleet-dispatch` also
reveals them during dispatch.

## Enforcement Mechanism

Server startup sequence:

```
1. Disable every conditional visibility tag at server construction
   → hides kitchen, headless, fleet, exploration, and evidence-reader surfaces

2. mcp.disable(tags={subset}) for each entry in config.subsets.disabled
   → e.g. hides all github-tagged tools if "github" is disabled

3. If AUTOSKILLIT_HEADLESS=1:
   mcp.enable(tags={"headless"})
   → reveals the HEADLESS_TOOLS entries

4. If a complete evidence-reader identity is present at startup:
   mcp.enable(tags={"evidence-reader"}, components={"tool"}, only=True)
   → reveals exactly the two brokers; a partial or malformed identity aborts startup

5. When open_kitchen is called:
   ctx.enable_components(tags={"kitchen"})   → reveals kitchen-tagged tools (not fleet)
   ctx.disable_components(tags={subset})     → re-hides each disabled subset
   (session-level enable overwrites server-level disable, so re-disabling is required)
```

## Defense in Depth (Headless Sessions)

Three independent layers prevent headless sessions from calling orchestration tools:

| Layer | Mechanism | What It Blocks |
|-------|-----------|----------------|
| 1. FastMCP | Kitchen tools remain hidden (`mcp.enable(headless)` does not reveal kitchen-only tools) | `run_skill`, `run_cmd`, `run_python`, `merge_worktree`, and all other kitchen-only tools |
| 2. Hook | `skill_orchestration_guard.py` PreToolUse hook | L1: `run_skill`, `run_cmd`, `run_python`; L3: `run_skill` |
| 3. Code | Exact and monotonic guards in `tools_execution/` | exact-L2 `run_skill`; L2-or-higher `run_cmd` and `run_python` |

All three layers must independently agree before any orchestration tool can execute.
A bypassed hook is caught by the code guard; a bypassed code guard is caught by the
missing kitchen visibility.

Session-scope refusals use the structured `{status, code, response, detail}` envelope.
`detail` is empty for ordinary refusal and identifies a typed exploration failure when
one occurs.

## Complete MCP Tool Access Control Map

Registered tools grouped by access tier. Source paths are relative to `src/autoskillit/`;
tag abbreviations are defined in the glossary above.

### FREE RANGE

| Tool | Tags | Source File |
|------|------|-------------|
| `open_kitchen` | AS | `server/tools/tools_kitchen/_open_kitchen/_orchestrator.py` |
| `close_kitchen` | AS | `server/tools/tools_kitchen/_close_kitchen.py` |
| `disable_quota_guard` | AS | `server/tools/tools_kitchen/_disable_quota_guard.py` |
| `enable_exploration` | AS | `server/tools/tools_exploration.py` |
| `reload_session` | AS | `server/tools/tools_kitchen/_reload_session.py` |
| `configure_fleet` | AS | `server/tools/tools_config.py` |
| `configure_order` | AS | `server/tools/tools_config.py` |
| `lock_ingredients` | AS | `server/tools/tools_kitchen/_lock_ingredients.py` |
| `declare_join_batch` | AS | `server/tools/tools_kitchen/_declare_join_batch.py` |

### HEADLESS-TAGGED

| Tool | Tags | Source File |
|------|------|-------------|
| `test_check` | AS, K, HL, KC | `server/tools/tools_workspace.py` |
| `unlock_agent_pack` | AS, K, HL, KC | `server/tools/tools_agents.py` |
| `commit_files` | AS, K, HL, KC | `server/tools/tools_workspace.py` |
| `fetch_github_issue` | AS, K, HL, FD, GH | `server/tools/tools_github.py` |
| `get_issue_title` | AS, K, HL, FD, GH | `server/tools/tools_github.py` |
| `bind_plan_set` | AS, K, HL, KC | `server/tools/tools_plan_set.py` |
| `write_audit_semantic_result` | AS, K, HL, KC | `server/tools/tools_audit_artifacts.py` |
| `write_standalone_audit_evidence` | AS, K, HL, KC | `server/tools/tools_audit_artifacts.py` |
| `write_audit_disposition_bundle` | AS, K, HL, KC | `server/tools/tools_audit_artifacts.py` |
| `post_pr_review` | AS, HL, GH | `server/tools/tools_pr_ops.py` |
| `delegate_evidence_reader` | AS, K, HL, KC | `server/tools/tools_evidence_reader.py` |

### AUTHENTICATED EVIDENCE READER

| Tool | Tags | Source File |
|------|------|-------------|
| `read_authorized_artifact` | AS, ER | `server/tools/tools_evidence_reader.py` |
| `get_authorized_artifact_page` | AS, ER | `server/tools/tools_evidence_reader.py` |

### EXPLORATION BROKERS

| Tool | Tags | Source File |
|------|------|-------------|
| `submit_exploration_query` | AS, K, EX | `server/tools/tools_exploration.py` |
| `get_exploration_page` | AS, K, EX | `server/tools/tools_exploration.py` |
| `resume_exploration_context` | AS, K, EX | `server/tools/tools_exploration.py` |

### KITCHEN — Execution

| Tool | Tags | Source File |
|------|------|-------------|
| `run_cmd` | AS, K, KC | `server/tools/tools_execution/_run_cmd.py` |
| `run_python` | AS, K, KC | `server/tools/tools_execution/_run_python.py` |
| `run_skill` | AS, K, KC | `server/tools/tools_execution/_run_skill_dispatch.py` |
| `run_fixed_batch` | AS, K, KC | `server/tools/tools_execution/_fixed_batch_handlers.py` |
| `read_fixed_batch_result` | AS, K, KC | `server/tools/tools_execution/_fixed_batch_handlers.py` |
| `recover_run_skill_result` | AS, K, KC | `server/tools/tools_pipeline_tracker/_handlers.py` |
| `complete_run_skill_result` | AS, K, KC | `server/tools/tools_pipeline_tracker/_handlers.py` |
| `record_pipeline_step` | AS, K, KC | `server/tools/tools_pipeline_tracker/_handlers.py` |

`run_fixed_batch` supervises attested managed-Codex assignments. Its companion
`read_fixed_batch_result` authorizes each bounded result page against the parent,
batch, assignment, source artifact, and digest.

### KITCHEN — Git / Workspace

| Tool | Tags | Source File |
|------|------|-------------|
| `merge_worktree` | AS, K, KC | `server/tools/tools_git.py` |
| `classify_fix` | AS, K, KC | `server/tools/tools_git.py` |
| `create_unique_branch` | AS, K, GH | `server/tools/tools_git.py` |
| `create_and_publish_branch` | AS, K, GH | `server/tools/tools_git.py` |
| `check_pr_mergeable` | AS, K, GH | `server/tools/tools_git.py` |
| `reset_test_dir` | AS, K, KC | `server/tools/tools_workspace.py` |
| `reset_workspace` | AS, K, KC | `server/tools/tools_workspace.py` |

### KITCHEN — Clone Operations

| Tool | Tags | Source File |
|------|------|-------------|
| `clone_repo` | AS, K, CL | `server/tools/tools_clone.py` |
| `remove_clone` | AS, K, CL | `server/tools/tools_clone.py` |
| `push_to_remote` | AS, K, GH | `server/tools/tools_clone.py` |
| `register_clone_status` | AS, K, CL | `server/tools/tools_clone.py` |
| `bootstrap_clone` | AS, K, CL | `server/tools/tools_clone.py` |

### KITCHEN — CI / Merge Queue

| Tool | Tags | Source File |
|------|------|-------------|
| `wait_for_ci` | AS, K, CI | `server/tools/tools_ci_watch.py` |
| `get_ci_status` | AS, K, CI | `server/tools/tools_ci_watch.py` |
| `wait_for_merge_queue` | AS, K, CI | `server/tools/tools_ci_merge_queue.py` |
| `check_repo_merge_state` | AS, K, CI | `server/tools/tools_ci.py` |
| `toggle_auto_merge` | AS, K, CI | `server/tools/tools_ci_merge_queue.py` |
| `enqueue_pr` | AS, K, CI | `server/tools/tools_ci_merge_queue.py` |
| `set_commit_status` | AS, K, GH | `server/tools/tools_ci.py` |

### KITCHEN — GitHub Integrations

| Tool | Tags | Source File |
|------|------|-------------|
| `report_bug` | AS, K, GH | `server/tools/tools_github.py` |
| `prepare_issue` | AS, K, GH | `server/tools/tools_issue_headless.py` |
| `claim_issue` | AS, K, GH | `server/tools/tools_issue_labels.py` |
| `release_issue` | AS, K, GH | `server/tools/tools_issue_labels.py` |
| `claim_and_resolve_issue` | AS, K, GH | `server/tools/tools_issue_composite.py` |
| `get_pr_reviews` | AS, K, GH | `server/tools/tools_pr_ops.py` |
| `bulk_close_issues` | AS, K, GH | `server/tools/tools_pr_ops.py` |
| `verify_review_receipt` | AS, K, GH | `server/tools/tools_pr_ops.py` |

### KITCHEN — Status / Telemetry

| Tool | Tags | Source File |
|------|------|-------------|
| `kitchen_status` | AS, K, KC | `server/tools/tools_status.py` |
| `write_telemetry_files` | AS, K, KC, TL | `server/tools/tools_status.py` |
| `read_db` | AS, K, KC | `server/tools/tools_status.py` |
| `analyze_tool_sequences` | AS, K, KC, TL | `server/tools/tools_status.py` |
| `inspect_session_logs` | AS, K, KC | `server/tools/tools_session_logs.py` |

### KITCHEN — Recipes

| Tool | Tags | Source File |
|------|------|-------------|
| `list_recipes` | AS, K, KC, FD | `server/tools/tools_recipe.py` |
| `load_recipe` | AS, K, KC, FD | `server/tools/tools_recipe.py` |
| `get_recipe_section` | AS, K, KC | `server/tools/_recipe_section_handler.py` |
| `complete_recipe_initialization` | AS, K, KC | `server/tools/tools_recipe.py` |
| `validate_recipe` | AS, K, KC | `server/tools/tools_recipe.py` |
| `migrate_recipe` | AS, K, KC | `server/tools/tools_recipe.py` |

### KITCHEN — Fleet

| Tool | Tags | Source File |
|------|------|-------------|
| `batch_cleanup_clones` | AS, FL, CL | `server/tools/tools_clone.py` |
| `get_pipeline_report` | AS, KC, FL | `server/tools/tools_status.py` |
| `get_token_summary` | AS, KC, FL, TL | `server/tools/tools_status.py` |
| `get_timing_summary` | AS, KC, FL, TL | `server/tools/tools_status.py` |
| `get_quota_events` | AS, KC, FL, TL | `server/tools/tools_status.py` |
| `dispatch_food_truck` | AS, KC, FL | `server/tools/tools_fleet_dispatch/_handlers.py` |
| `record_gate_dispatch` | AS, KC, FL | `server/tools/tools_fleet_dispatch/_handlers.py` |
| `reset_dispatch` | AS, KC, FL | `server/tools/tools_fleet_reset.py` |

Tool visibility in the server and tool addressability in an interactive Claude
client are separate boundaries. `open_kitchen` is initially visible and carries
per-tool `anthropic/alwaysLoad` metadata, while kitchen-tagged tools remain
dynamically gated until opening completes. The bounded client snapshot and
fresh/resume behavior are documented in
[Claude startup readiness](claude-startup-readiness.md).

`GATED_TOOLS` and the category-specific sets in
`core/types/_type_constants_registries.py` define the visible surfaces. The kitchen
set is derived by subtracting fleet, exploration, and evidence-reader tools from
`GATED_TOOLS`.

For subset configuration that can hide functional-category tools, see
[Subset Categories](../skills/subsets.md).
