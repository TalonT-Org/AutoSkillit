# server/

IL-3 FastMCP server — MCP tools, kitchen gating, session-type dispatch.
Sub-packages: tools/ (see tools/AGENTS.md), recipe/ (see recipe/AGENTS.md),
lifecycle/ (see lifecycle/AGENTS.md), response/ (see response/AGENTS.md).

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
  - **ORCHESTRATOR/SKILL + interactive + non-notification backend**: lifespan boot runs `_pre_reveal_kitchen()`
  - **ORCHESTRATOR/SKILL + interactive + notification-capable backend**: nothing pre-revealed; `open_kitchen` reveals the `kitchen` tag
  - **Direct sessions without a registered ORCHESTRATOR/SKILL interactive handler**: not implicitly pre-revealed
- All tags in `ALL_VISIBILITY_TAGS` are disabled at startup via `for tag in sorted(ALL_VISIBILITY_TAGS): mcp.disable(tags={tag})`. Session-type dispatch and `open_kitchen` selectively re-enable per session. `ALL_VISIBILITY_TAGS` is defined in `core/types/_type_constants_registries.py`.

### Application-Gate (Python layer)

Controls whether the tool succeeds when called (independent of visibility):

- Most kitchen tools call `_require_enabled()` as their first statement, which checks `ctx.gate.enabled`
- Returns a `gate_error` JSON envelope if the kitchen hasn't been opened
- `_require_enabled()` is defined in `server/lifecycle/_guards.py`; the error envelope is defined in `pipeline/gate.py`
- Enforcement is validated by `test_gated_tools_call_require_enabled_first` in `tests/arch/test_layer_enforcement.py`

### The Anomalies

1. **Fleet-dispatch tools are hidden at startup (via the `ALL_VISIBILITY_TAGS` loop)
   and additionally revealed for FLEET+dispatch sessions.** INSPECTION tools may also
   carry `kitchen`, as enforced by `test_tool_decorators_enforce_tag_partition`.
   Application-gated members also call `_require_enabled()`.

2. **`test_check` is tag-hidden but NOT application-gated.** It carries the `kitchen`, `kitchen-core`,
   `headless`, and `autoskillit` tags (hidden at startup), but does NOT call `_require_enabled()`. Headless skill sessions need
   `test_check` without opening the kitchen — the `headless` tag provides visibility in SKILL sessions,
   and skipping `_require_enabled()` lets the call succeed without a gate open.

### Tool Gating Matrix

| Category | Tag(s) | Hidden at startup? | Application-gated? | Example tools |
|----------|--------|-------------------|--------------------|--------------|
| Standard kitchen | `kitchen` | Yes | Yes (`_require_enabled`) | `run_cmd`, `run_skill`, `report_bug` |
| Fleet tool | `fleet`, `kitchen-core` | Yes (via `ALL_VISIBILITY_TAGS` loop) | Yes (`_require_fleet` or `_require_enabled`) | `dispatch_food_truck`, `record_gate_dispatch` |
| Fleet-dispatch tool | `kitchen`, `fleet-dispatch` (± `kitchen-core`) | Yes (via `ALL_VISIBILITY_TAGS` loop) | Yes (`_require_enabled`) | `list_recipes`, `load_recipe` |
| Headless-exempt | `headless` (usually `kitchen`) | Yes | No | `test_check`, `commit_files`, `fetch_github_issue`, `get_issue_title`, typed audit artifact producers |
| Exploration broker | `exploration` | Yes (via `ALL_VISIBILITY_TAGS` loop) | Yes (`_require_enabled`) | `submit_exploration_query`, `get_exploration_page`, `resume_exploration_context` |
| Free-range | _(none of the above)_ | No | No | `open_kitchen`, `close_kitchen` |

### Exploration Tag Visibility

Tag visibility for the three exploration broker tools is UX defense-in-depth — the per-call
HMAC capability lease is the authorization boundary. Visibility of the read-only brokers in
intent-provisioned sessions is deliberate and safe because every call re-verifies the lease.

**Backend asymmetry:** Codex confines the `shared-explorer-session` principal to a dedicated
read-only child process via per-child env binding. Claude's tag enablement
(`mcp.enable(tags={"exploration"})`) is process-wide on the shared MCP connection — every
subagent in an intent-provisioned session can see (though not use without the lease) the
three brokers. This larger ambient-visibility surface is a deliberate trade-off, acceptable
because the surface is read-only and the per-call lease is the enforcement point.

### Registry Constants

The canonical tool sets are in `core/types/_type_constants_registries.py`:

- `GATED_TOOLS` — all tools that call `_require_enabled()` (validated by arch test)
- `UNGATED_TOOLS` = `FREE_RANGE_TOOLS` — tools with no gating at all
- `HEADLESS_TOOLS` — the source-of-truth set of application-ungated tools available to
  headless skill sessions, including testing, commit, issue readers, and audit workers
- `FLEET_TOOLS` — fleet-session-only tools
- `FLEET_DISPATCH_TOOLS` — tools additionally revealed in dispatch mode; only
  INSPECTION members may also carry `kitchen`
- `ALL_VISIBILITY_TAGS` — the source-of-truth set of conditional visibility tags disabled at startup
- Tool tag and tier changes are checked against bundled skill and recipe consumers by
  `tests/server/test_consumer_tool_callability.py`; each `EXEMPTIONS` entry needs a
  cited PR, issue, or `path:line`
