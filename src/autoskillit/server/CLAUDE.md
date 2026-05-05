# server/

IL-3 FastMCP server — MCP tools, kitchen gating, session-type dispatch.
Sub-package: tools/ (see tools/CLAUDE.md).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-exports `mcp`, `ToolContext`, `make_context`; applies `mcp.disable(tags={'kitchen'})` at import |
| `_editable_guard.py` | Pre-deletion editable install guard for `perform_merge()` — scans site-packages for PEP 610 direct_url.json links into the worktree |
| `_factory.py` | Composition root — `make_context()` is the sole legal instantiation point for all 22 service contracts |
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
