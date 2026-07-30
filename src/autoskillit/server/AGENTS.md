# server/

IL-3 FastMCP server — MCP tools, kitchen gating, session-type dispatch.
Sub-package: tools/ (see tools/AGENTS.md).

The composition root `make_context()` is the sole legal instantiation point for all
service contracts.

## readOnlyHint: MCP tools default to `readOnlyHint: True`

Every pipeline operates on independent branches and worktrees with zero cross-pipeline
interference. `readOnlyHint: False` serializes parallel tool calls and causes catastrophic
pipeline slowdowns (40+ minutes instead of 5 minutes for concurrent CI watches).

This has regressed three times. Defense-in-depth:
- Pre-commit: `scripts/check_tool_annotations.py` (AST scan, blocks commit)
- Tests: `test_all_tools_have_readonly_hint_true` (universal assertion, no registry)
- Tests: `test_all_annotations_are_readonly_true` (AST-level, no server import)

`open_kitchen` is the sole exception: it uses `readOnlyHint: False` because it mutates
the process-local gate, visibility, hook configuration, trackers, and replay journal.
Every other tool remains `readOnlyHint: True`; adding another exception requires an
explicit architectural contract change.

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
- All tags in `ALL_VISIBILITY_TAGS` are disabled at startup via `for tag in sorted(ALL_VISIBILITY_TAGS): mcp.disable(tags={tag})`. Session-type dispatch and `open_kitchen` selectively re-enable per session. `ALL_VISIBILITY_TAGS` is defined in `core/types/_type_constants_registries.py`.

### Application-Gate (Python layer)

Controls whether the tool succeeds when called (independent of visibility):

- Most kitchen tools call `_require_enabled()` as their first statement, which checks `ctx.gate.enabled`
- Returns a `gate_error` JSON envelope if the kitchen hasn't been opened
- `_require_enabled()` is defined in `server/_guards.py`; the error envelope is defined in `pipeline/gate.py`
- Enforcement is validated by `test_gated_tools_call_require_enabled_first` in `tests/arch/test_layer_enforcement.py`

### The Anomalies

1. **Fleet-dispatch tools are hidden at startup (via `ALL_VISIBILITY_TAGS` loop) and revealed only for FLEET+dispatch sessions.** Application-gate (`_require_enabled()`) provides defense-in-depth.

2. **`test_check` is tag-hidden but NOT application-gated.** It carries the `kitchen`, `kitchen-core`,
   `headless`, and `autoskillit` tags (hidden at startup), but does NOT call `_require_enabled()`. Headless skill sessions need
   `test_check` without opening the kitchen — the `headless` tag provides visibility in SKILL sessions,
   and skipping `_require_enabled()` lets the call succeed without a gate open.

### Tool Gating Matrix

| Category | Tag(s) | Hidden at startup? | Application-gated? | Example tools |
|----------|--------|-------------------|--------------------|--------------|
| Standard kitchen | `kitchen` | Yes | Yes (`_require_enabled`) | `run_cmd`, `run_skill`, `report_bug` |
| Fleet tool | `fleet`, `kitchen-core` | Yes (via `ALL_VISIBILITY_TAGS` loop) | Yes (`_require_fleet` or `_require_enabled`) | `dispatch_food_truck`, `record_gate_dispatch` |
| Fleet-dispatch tool | `fleet-dispatch` (± `kitchen-core`) | Yes (via `ALL_VISIBILITY_TAGS` loop) | Yes (`_require_enabled`) | `fetch_github_issue`, `list_recipes` |
| Headless-exempt | `kitchen`, `headless` | Yes | No | `test_check`, `commit_files`, `unlock_agent_pack`, typed audit artifact producers |
| Free-range | _(none of the above)_ | No | No | `open_kitchen`, `close_kitchen` |

### Registry Constants

The canonical tool sets are in `core/types/_type_constants_registries.py`:

- `GATED_TOOLS` — all tools that call `_require_enabled()` (validated by arch test)
- `UNGATED_TOOLS` = `FREE_RANGE_TOOLS` — tools with no gating at all
- `HEADLESS_TOOLS` — the seven kitchen-tagged, application-ungated worker tools: testing, commit, legacy audit-cycle, and the three typed audit artifact producers
- `FLEET_TOOLS` — fleet-session-only tools
- `FLEET_DISPATCH_TOOLS` — fleet-dispatch-mode tools (hidden at startup, application-gated)
- `ALL_VISIBILITY_TAGS` — `{"kitchen", "headless", "fleet", "fleet-dispatch", "kitchen-core", "plan-review"}`
