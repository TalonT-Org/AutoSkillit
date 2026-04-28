# Server Layer Rules

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
