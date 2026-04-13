# MCP Tool Access Control

AutoSkillit provides 43 MCP tools organized into three access levels that control which
session types can see each tool.

## Three Access Levels

```
┌─────────────────────────────────────────────────────────┐
│  FREE RANGE  (2 tools, always visible)                  │
│  open_kitchen, close_kitchen                            │
│  Always visible — no gating, no headless restriction    │
├─────────────────────────────────────────────────────────┤
│  HEADLESS-TAGGED  (1 tool)                              │
│  test_check                                             │
│  Revealed in headless sessions via mcp.enable(headless) │
│  Also carries the kitchen tag; hidden in plain sessions │
├─────────────────────────────────────────────────────────┤
│  KITCHEN  (36 tools)                                    │
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

Note: Disabled subsets further restrict visibility within the Kitchen tier — their tools
remain hidden even after `open_kitchen`.

## FastMCP Tag Glossary

| Tag | Meaning |
|-----|---------|
| `autoskillit` | Identifies the tool as belonging to AutoSkillit. Present on every tool. |
| `kitchen` | Tool is hidden at startup via `mcp.disable(tags={'kitchen'})`. 38 tools carry this tag. |
| `headless` | Tool is revealed in headless sessions via `mcp.enable(tags={'headless'})`. Additive — also carries `kitchen`. |
| `github` | Functional category: GitHub-interacting tools. Can be disabled as a subset. |
| `ci` | Functional category: CI/merge-queue polling tools. Can be disabled as a subset. |
| `clone` | Functional category: Clone-based isolation tools. Can be disabled as a subset. |
| `telemetry` | Functional category: Token, timing, and quota reporting tools. Can be disabled as a subset. |

## Enforcement Mechanism

Server startup sequence:

```
1. mcp.disable(tags={"kitchen"})
   → hides 38 kitchen-tagged tools (including the 1 headless-tagged tool)

2. mcp.disable(tags={subset}) for each entry in config.subsets.disabled
   → e.g. hides all github-tagged tools if "github" is disabled

3. If AUTOSKILLIT_HEADLESS=1:
   mcp.enable(tags={"headless"})
   → reveals test_check only (the sole headless-tagged tool)

4. When open_kitchen is called:
   ctx.enable_components(tags={"kitchen"})   → reveals all 41 kitchen tools
   ctx.disable_components(tags={subset})     → re-hides each disabled subset
   (session-level enable overwrites server-level disable, so re-disabling is required)
```

## Defense in Depth (Headless Sessions)

Three independent layers prevent headless sessions from calling orchestration tools:

| Layer | Mechanism | What It Blocks |
|-------|-----------|----------------|
| 1. FastMCP | Kitchen tools remain hidden (`mcp.enable(headless)` does not reveal kitchen-only tools) | `run_skill`, `run_cmd`, `run_python`, `merge_worktree`, and all other kitchen-only tools |
| 2. Hook | `headless_orchestration_guard.py` PreToolUse hook | `run_skill`, `run_cmd`, `run_python` |
| 3. Code | `_require_not_headless()` guard in `tools_execution.py` | `run_skill`, `run_cmd`, `run_python` |

All three layers must independently agree before any orchestration tool can execute.
A bypassed hook is caught by the code guard; a bypassed code guard is caught by the
missing kitchen visibility.

## Complete MCP Tool Access Control Map

All 43 tools with their access level, tags, source file, and functional category.

**Tag abbreviations**: AS = `autoskillit`, K = `kitchen`, HL = `headless`,
GH = `github`, CI = `ci`, CL = `clone`, TL = `telemetry`

---

### FREE RANGE

| Tool | Tags | Source File |
|------|------|-------------|
| `open_kitchen` | AS | `server/tools_kitchen.py` |
| `close_kitchen` | AS | `server/tools_kitchen.py` |

---

### HEADLESS-TAGGED

| Tool | Tags | Source File | Notes |
|------|------|-------------|-------|
| `test_check` | AS, K, HL | `server/tools_workspace.py` | Only tool revealed in headless sessions |

---

### KITCHEN — Orchestration Triad

| Tool | Tags | Source File |
|------|------|-------------|
| `run_cmd` | AS, K | `server/tools_execution.py` |
| `run_python` | AS, K | `server/tools_execution.py` |
| `run_skill` | AS, K | `server/tools_execution.py` |

---

### KITCHEN — Git / Workspace

| Tool | Tags | Source File |
|------|------|-------------|
| `merge_worktree` | AS, K | `server/tools_git.py` |
| `classify_fix` | AS, K | `server/tools_git.py` |
| `create_unique_branch` | AS, K, GH | `server/tools_git.py` |
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
| `batch_cleanup_clones` | AS, K, CL | `server/tools_clone.py` |

---

### KITCHEN — CI / Merge Queue

| Tool | Tags | Source File |
|------|------|-------------|
| `wait_for_ci` | AS, K, CI | `server/tools_ci.py` |
| `get_ci_status` | AS, K, CI | `server/tools_ci.py` |
| `wait_for_merge_queue` | AS, K, CI | `server/tools_ci.py` |
| `check_repo_merge_state` | AS, K, CI | `server/tools_ci.py` |
| `toggle_auto_merge` | AS, K, CI | `server/tools_ci.py` |
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

---

### KITCHEN — Recipes

| Tool | Tags | Source File |
|------|------|-------------|
| `list_recipes` | AS, K | `server/tools_recipe.py` |
| `load_recipe` | AS, K | `server/tools_recipe.py` |
| `validate_recipe` | AS, K | `server/tools_recipe.py` |
| `migrate_recipe` | AS, K | `server/tools_recipe.py` |

---

**Total: 43 tools** — 2 Free Range + 41 Kitchen-tagged (of which 1, `test_check`, additionally carries the `headless` tag and is revealed inside headless sessions)

For subset configuration that can hide functional-category tools, see
[Subset Categories](../skills/subsets.md).
