# server/

IL-3 FastMCP server — MCP tools, kitchen gating, session-type dispatch.
Sub-package: tools/ (see tools/CLAUDE.md).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-exports `mcp`, `ToolContext`, `make_context`; applies `mcp.disable(tags={'kitchen'})` at import |
| `_editable_guard.py` | Pre-deletion editable install guard for `perform_merge()` — scans site-packages for PEP 610 direct_url.json links into the worktree |
| `_factory.py` | Composition root — `make_context()` is the sole legal instantiation point for all 23 service contracts |
| `_guards.py` | Orchestration-level gate functions for MCP tool access control |
| `_lifespan.py` | FastMCP lifespan context manager — deferred startup (recovery, audit loading, stale cleanup, drift check) |
| `_misc.py` | Quota, hook-config, triage, and miscellaneous server utilities; re-exports selected execution/workspace symbols for tools |
| `_notify.py` | MCP notification dispatch and response-size tracking |
| `_session_type.py` | Session-type tag visibility dispatcher — controls which tools are visible per session type |
| `_state.py` | Mutable singleton state and context accessor functions (`_ctx` sentinel, `get_ctx`, `set_ctx`) |
| `_subprocess.py` | Subprocess execution helpers for MCP tools |
| `_wire_compat.py` | Wire-format compatibility middleware — strips `outputSchema`/`title` fields to work around Claude Code bug #25081 |
| `git.py` | Git merge workflow for `merge_worktree` — path validation, branch detection, test gate, fetch, rebase, merge, cleanup |

## readOnlyHint: All MCP tools MUST have `readOnlyHint: True`

Every pipeline operates on independent branches and worktrees with zero cross-pipeline
interference. `readOnlyHint: False` serializes parallel tool calls and causes catastrophic
pipeline slowdowns (40+ minutes instead of 5 minutes for concurrent CI watches).

This has regressed three times. Defense-in-depth:
- Pre-commit: `scripts/check_tool_annotations.py` (AST scan, blocks commit)
- Tests: `test_all_tools_have_readonly_hint_true` (universal assertion, no registry)
- Tests: `test_all_annotations_are_readonly_true` (AST-level, no server import)

If you believe a tool genuinely needs `readOnlyHint: False`, you are wrong. All pipelines
use independent branches. There is no shared mutable state between concurrent tool calls.

## Tool Gating Architecture

Tools are controlled by two independent mechanisms. A tool may be affected by one, both, or neither.

### Tag-Visibility (FastMCP layer)

Controls whether the tool appears in `tools/list` (whether the agent can see it):

- `mcp.disable(tags={"kitchen"})` at startup hides all `kitchen`-tagged tools
- `_apply_session_type_visibility()` selectively reveals tags per session type:
  - **FLEET** sessions: `fleet`-tagged tools revealed; `fleet-dispatch` revealed only in dispatch mode
  - **ORCHESTRATOR + HEADLESS**: `kitchen` (or `kitchen-core` + pack tags) revealed
  - **SKILL + HEADLESS**: `headless`-tagged tools revealed (`test_check`); with `HEADLESS_AUTO_GATE=1`, `kitchen-core` also revealed
  - **Interactive** (no HEADLESS): nothing pre-revealed; `open_kitchen` reveals `kitchen` tag
- Tags not disabled at startup (`kitchen-core`, `fleet-dispatch`, `fleet`, `headless`) remain
  visible unless a session-type or feature-gate transform explicitly disables them
- `ALL_VISIBILITY_TAGS` in `core/types/_type_constants.py` is the canonical set:
  `{"kitchen", "headless", "fleet", "fleet-dispatch", "kitchen-core"}`

### Application-Gate (Python layer)

Controls whether the tool succeeds when called (independent of visibility):

- Most kitchen tools call `_require_enabled()` as their first statement, which checks `ctx.gate.enabled`
- Returns a `gate_error` JSON envelope if the kitchen hasn't been opened
- `_require_enabled()` is defined in `server/_guards.py`; the error envelope is defined in `pipeline/gate.py`
- Enforcement is validated by `test_gated_tools_call_require_enabled_first` in `tests/arch/test_layer_enforcement.py`

### The Anomalies

1. **Fleet-dispatch tools are tag-visible but application-gated.** `fetch_github_issue`, `get_issue_title`,
   `list_recipes`, and `load_recipe` carry the `fleet-dispatch` tag (not `kitchen`), so they are
   NOT hidden by `mcp.disable(tags={"kitchen"})`. In interactive sessions they appear in `tools/list`
   without `open_kitchen`. But they call `_require_enabled()` internally — an agent that sees them
   and calls them gets an unexpected gate error.

2. **`test_check` is tag-hidden but NOT application-gated.** It carries the `kitchen`, `kitchen-core`,
   `headless`, and `autoskillit` tags (hidden at startup), but does NOT call `_require_enabled()`. Headless skill sessions need
   `test_check` without opening the kitchen — the `headless` tag provides visibility in SKILL sessions,
   and skipping `_require_enabled()` lets the call succeed without a gate open.

### Tool Gating Matrix

| Category | Tag(s) | Hidden at startup? | Application-gated? | Example tools |
|----------|--------|-------------------|--------------------|--------------|
| Standard kitchen | `kitchen` | Yes | Yes (`_require_enabled`) | `run_cmd`, `run_skill`, `report_bug` |
| Fleet tool | `fleet`, `kitchen-core` | No (no `kitchen` tag) | Yes (`_require_fleet` or `_require_enabled`) | `dispatch_food_truck`, `record_gate_dispatch` |
| Fleet-dispatch tool | `fleet-dispatch` (± `kitchen-core`) | No (no `kitchen` tag) | Yes (`_require_enabled`) | `fetch_github_issue`, `list_recipes` |
| Headless-exempt | `kitchen`, `headless` | Yes | No | `test_check` |
| Free-range | _(none of the above)_ | No | No | `open_kitchen`, `close_kitchen` |

### Registry Constants

The canonical tool sets are in `core/types/_type_constants.py`:

- `GATED_TOOLS` — all tools that call `_require_enabled()` (validated by arch test)
- `UNGATED_TOOLS` = `FREE_RANGE_TOOLS` — tools with no gating at all
- `HEADLESS_TOOLS` — `{"test_check"}` — kitchen-tagged but not application-gated
- `FLEET_TOOLS` — fleet-session-only tools
- `FLEET_DISPATCH_TOOLS` — fleet-dispatch-mode tools (always tag-visible, application-gated)
- `ALL_VISIBILITY_TAGS` — `{"kitchen", "headless", "fleet", "fleet-dispatch", "kitchen-core"}`
