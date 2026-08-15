# MCP Tool Access Control

AutoSkillit provides 75 MCP tools organized into three access levels that control which
session types can see each tool.

## Three Access Levels

```
┌─────────────────────────────────────────────────────────┐
│  FREE RANGE  (4 tools, always visible)                  │
│  open_kitchen, close_kitchen, disable_quota_guard,      │
│  reload_session                                         │
│  Always visible — no gating, no headless restriction    │
├─────────────────────────────────────────────────────────┤
│  HEADLESS-TAGGED  (8 tools)                             │
│  test/check, commit, audit, and review worker tools     │
│  Revealed in headless sessions via mcp.enable(headless) │
│  Also carries the kitchen tag; hidden in plain sessions │
├─────────────────────────────────────────────────────────┤
│  KITCHEN  (45 kitchen-only tools)                       │
│  All remaining tools                                    │
│  Hidden at startup; revealed when open_kitchen is called│
└─────────────────────────────────────────────────────────┘
```

## Session Mode Access Matrix

| Session Mode | Free Range | Kitchen tools | Headless-tagged |
|---|---|---|---|
| `$ claude` (plugin, no kitchen) | ✓ | ✗ | ✗ |
| `$ claude` (after `/open-kitchen`) | ✓ | ✓ | ✗ |
| `$ autoskillit cook` (before `/open-kitchen`) | ✓ | ✗ | ✗ |
| `$ autoskillit cook` (after `/open-kitchen`) | ✓ | ✓ | ✗ |
| `$ autoskillit order` | ✓ | ✓ (pre-opened) | ✗ |
| `run_skill` (headless) | ✓ | ✗ | ✓ |
| L2 food truck | ✓ | ✓ (pre-opened) | ✗ |
| L3 fleet | ✓ | fleet surface | ✗ |

Note: Disabled subsets further restrict visibility within the Kitchen tier — their tools
remain hidden even after `open_kitchen`.

The two authenticated evidence-reader broker tools belong to a separate private surface.
They are not kitchen, free-range, or fleet tools and remain hidden until a future verified
reader binding enables the `evidence-reader` tag.

Visibility is not authority. At the application and hook layers, `run_skill` is restricted
to exact L2 `ORCHESTRATOR` sessions. L3 `FLEET` sessions create L2 food trucks through
`dispatch_food_truck`; they retain `run_cmd` and `run_python` but cannot call `run_skill`.

## FastMCP Tag Glossary

| Tag | Meaning |
|-----|---------|
| `autoskillit` | Identifies the tool as belonging to AutoSkillit. Present on every tool. |
| `kitchen` | Tool is hidden at startup via `mcp.disable(tags={'kitchen'})`. 52 tools carry this tag. |
| `headless` | Tool is revealed in headless sessions via `mcp.enable(tags={'headless'})`. Most also carry `kitchen`; `post_pr_review` is headless-only and deliberately ungated. |
| `evidence-reader` | Authenticated artifact brokers enabled only by a verified reader binding. |
| `github` | Functional category: GitHub-interacting tools. Can be disabled as a subset. |
| `ci` | Functional category: CI/merge-queue polling tools. Can be disabled as a subset. |
| `clone` | Functional category: Clone-based isolation tools. Can be disabled as a subset. |
| `telemetry` | Functional category: Token, timing, and quota reporting tools. Can be disabled as a subset. |

## Enforcement Mechanism

Server startup sequence:

```
1. mcp.disable(tags={"kitchen"})
   → hides 52 kitchen-tagged tools (including the 7 headless-tagged tools)

2. mcp.disable(tags={subset}) for each entry in config.subsets.disabled
   → e.g. hides all github-tagged tools if "github" is disabled

3. If AUTOSKILLIT_HEADLESS=1:
   mcp.enable(tags={"headless"})
   → reveals the eight HEADLESS_TOOLS entries

4. When open_kitchen is called:
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
| 3. Code | Exact and monotonic guards in `tools_execution.py` | exact-L2 `run_skill`; L2-or-higher `run_cmd` and `run_python` |

All three layers must independently agree before any orchestration tool can execute.
A bypassed hook is caught by the code guard; a bypassed code guard is caught by the
missing kitchen visibility.

## Complete MCP Tool Access Control Map

All 75 tools with their access level, tags, source file, and functional category.

**Tag abbreviations**: AS = `autoskillit`, K = `kitchen`, HL = `headless`,
ER = `evidence-reader`, GH = `github`, CI = `ci`, CL = `clone`,
TL = `telemetry`, FL = `fleet`

---

### FREE RANGE

| Tool | Tags | Source File |
|------|------|-------------|
| `open_kitchen` | AS | `server/tools_kitchen.py` |
| `close_kitchen` | AS | `server/tools_kitchen.py` |
| `disable_quota_guard` | AS | `server/tools_kitchen.py` |
| `reload_session` | AS | `server/tools_kitchen.py` |
| `configure_fleet` | AS | `server/tools_config.py` |
| `configure_order` | AS | `server/tools_config.py` |

---

### HEADLESS-TAGGED

| Tool | Tags | Source File | Notes |
|------|------|-------------|-------|
| `test_check` | AS, K, HL | `server/tools_workspace.py` | Test runner |
| `unlock_agent_pack` | AS, K, HL | `server/tools_agents.py` | Agent-pack access |
| `commit_files` | AS, K, HL | `server/tools_git.py` | Server-side commit |
| `write_audit_semantic_result` | AS, K, HL | `server/tools_audit_artifacts.py` | Typed audit semantics |
| `write_standalone_audit_evidence` | AS, K, HL | `server/tools_audit_artifacts.py` | Standalone evidence |
| `write_audit_disposition_bundle` | AS, K, HL | `server/tools_audit_artifacts.py` | Typed disposition bundle |
| `post_pr_review` | AS, HL, GH | `server/tools_pr_ops.py` | PR review worker |
| `delegate_evidence_reader` | AS, K, HL | `server/tools_evidence_reader.py` | Authenticated reader delegation |

---

### AUTHENTICATED EVIDENCE READER

| Tool | Tags | Source File | Notes |
|------|------|-------------|-------|
| `read_authorized_artifact` | AS, ER | `server/tools_evidence_reader.py` | Bounded artifact read |
| `get_authorized_artifact_page` | AS, ER | `server/tools_evidence_reader.py` | Authorized page retrieval |

---

### KITCHEN — Execution

| Tool | Tags | Source File |
|------|------|-------------|
| `run_cmd` | AS, K | `server/tools_execution.py` |
| `run_python` | AS, K | `server/tools_execution.py` |
| `run_skill` | AS, K | `server/tools_execution.py` |
| `recover_run_skill_result` | AS, K | `server/tools_pipeline_tracker.py` |
| `complete_run_skill_result` | AS, K | `server/tools_pipeline_tracker.py` |

---

### KITCHEN — Git / Workspace

| Tool | Tags | Source File |
|------|------|-------------|
| `merge_worktree` | AS, K | `server/tools_git.py` |
| `classify_fix` | AS, K | `server/tools_git.py` |
| `create_unique_branch` | AS, K, GH | `server/tools_git.py` |
| `create_and_publish_branch` | AS, K, GH | `server/tools_git.py` |
| `check_pr_mergeable` | AS, K, GH | `server/tools_git.py` |
| `reset_test_dir` | AS, K | `server/tools_workspace.py` |
| `reset_workspace` | AS, K | `server/tools_workspace.py` |

---

### KITCHEN — Clone Operations

| Tool | Tags | Source File |
|------|------|-------------|
| `clone_repo` | AS, K, CL | `server/tools_clone.py` |
| `remove_clone` | AS, K, CL | `server/tools_clone.py` |
| `push_to_remote` | AS, K, GH | `server/tools_clone.py` |
| `register_clone_status` | AS, K, CL | `server/tools_clone.py` |
| `batch_cleanup_clones` | AS, K, CL, FL | `server/tools_clone.py` |
| `bootstrap_clone` | AS, K, CL | `server/tools_clone.py` |

---

### KITCHEN — CI / Merge Queue

| Tool | Tags | Source File |
|------|------|-------------|
| `wait_for_ci` | AS, K, CI | `server/tools_ci.py` |
| `get_ci_status` | AS, K, CI | `server/tools_ci.py` |
| `wait_for_merge_queue` | AS, K, CI | `server/tools_ci.py` |
| `check_repo_merge_state` | AS, K, CI | `server/tools_ci.py` |
| `toggle_auto_merge` | AS, K, CI | `server/tools_ci.py` |
| `enqueue_pr` | AS, K, CI | `server/tools_ci.py` |
| `set_commit_status` | AS, K, GH | `server/tools_ci.py` |

---

### KITCHEN — GitHub Integrations

| Tool | Tags | Source File |
|------|------|-------------|
| `fetch_github_issue` | AS, K, GH | `server/tools_github.py` |
| `get_issue_title` | AS, K, GH | `server/tools_github.py` |
| `report_bug` | AS, K, GH | `server/tools_github.py` |
| `prepare_issue` | AS, K, GH | `server/tools_issue_lifecycle.py` |
| `enrich_issues` | AS, K, GH | `server/tools_issue_lifecycle.py` |
| `claim_issue` | AS, K, GH | `server/tools_issue_lifecycle.py` |
| `release_issue` | AS, K, GH | `server/tools_issue_lifecycle.py` |
| `claim_and_resolve_issue` | AS, K, GH | `server/tools_issue_composite.py` |
| `get_pr_reviews` | AS, K, GH | `server/tools_pr_ops.py` |
| `bulk_close_issues` | AS, K, GH | `server/tools_pr_ops.py` |

---

### KITCHEN — Status / Telemetry

| Tool | Tags | Source File |
|------|------|-------------|
| `kitchen_status` | AS, K | `server/tools_status.py` |
| `get_pipeline_report` | AS, K | `server/tools_status.py` |
| `get_token_summary` | AS, K, TL | `server/tools_status.py` |
| `get_timing_summary` | AS, K, TL | `server/tools_status.py` |
| `get_quota_events` | AS, K, TL | `server/tools_status.py` |
| `write_telemetry_files` | AS, K, TL | `server/tools_status.py` |
| `read_db` | AS, K | `server/tools_status.py` |
| `analyze_tool_sequences` | AS, K, TL | `server/tools_status.py` |

---

### KITCHEN — Recipes

| Tool | Tags | Source File |
|------|------|-------------|
| `list_recipes` | AS, K | `server/tools_recipe.py` |
| `load_recipe` | AS, K | `server/tools_recipe.py` |
| `complete_recipe_initialization` | AS, K | `server/tools_recipe.py` |
| `validate_recipe` | AS, K | `server/tools_recipe.py` |
| `migrate_recipe` | AS, K | `server/tools_recipe.py` |

---

### KITCHEN — Fleet

| Tool | Tags | Source File |
|------|------|-------------|
| `dispatch_food_truck` | AS, K, KC, fleet | `server/tools_execution.py` |
| `record_gate_dispatch` | AS, K, KC, fleet | `server/tools_execution.py` |

---

Tool visibility in the server and tool addressability in an interactive Claude
client are separate boundaries. `open_kitchen` is initially visible and carries
per-tool `anthropic/alwaysLoad` metadata, while kitchen-tagged tools remain
dynamically gated until opening completes. The bounded client snapshot and
fresh/resume behavior are documented in
[Claude startup readiness](claude-startup-readiness.md).

**Total: 75 registered tools**. The 52 kitchen-tagged tools include seven of the eight
headless tools. The two authenticated evidence-reader brokers are excluded from the
kitchen, free-range, and fleet counts.

For subset configuration that can hide functional-category tools, see
[Subset Categories](../skills/subsets.md).
