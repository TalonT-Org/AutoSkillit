# pipeline/

IL-1 pipeline state — per-tool-call state containers, gate logic, audit log, telemetry.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-exports public protocol implementations |
| `audit.py` | `FailureRecord`, `DefaultAuditLog` |
| `background.py` | `DefaultBackgroundSupervisor` |
| `context.py` | `ToolContext` DI container |
| `gate.py` | `DefaultGateState`, `gate_error_result` |
| `github_api_log.py` | `DefaultGitHubApiLog` — session-scoped GitHub API request accumulator |
| `mcp_response.py` | Per-tool response size tracking |
| `telemetry_fmt.py` | Canonical token/timing display |
| `timings.py` | `DefaultTimingLog` |
| `tokens.py` | `DefaultTokenLog` |
| `pr_gates.py` | `is_ci_passing`, `is_review_passing`, `partition_prs` |

## Architecture Notes

`ToolContext` is the composition root injected into every MCP tool handler via
`server/_factory.py:make_context()`. All implementations satisfy protocols defined in
`core/types/`. `gate.py` is the sole source of structured gate-error results; no tool
handler constructs gate errors directly.
