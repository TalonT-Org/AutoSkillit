# pipeline/

Audit log, gate state, token tracking, and PR-gate tests.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `test_audit.py` | Tests for autoskillit.pipeline.audit — pipeline failure tracking |
| `test_background_supervisor.py` | Unit tests for DefaultBackgroundSupervisor |
| `test_context.py` | Tests for ToolContext dependency injection container |
| `test_gate.py` | Unit tests for _gate.py constants and functions |
| `test_github_api_log.py` | GitHub API log tests |
| `test_mcp_response.py` | Tests for autoskillit.pipeline.mcp_response — MCP tool response size tracking |
| `test_pr_gates.py` | Tests for analyze-prs PR eligibility gate logic (CI gate + review gate) |
| `test_telemetry_formatter.py` | Tests for TelemetryFormatter — canonical telemetry formatting |
| `test_timings.py` | Tests for autoskillit.pipeline.timings — pipeline step timing |
| `test_tokens_core.py` | Tests for pipeline.tokens — TokenEntry, DefaultTokenLog core, and log-dir loading |
| `test_tokens_filters.py` | Tests for pipeline.tokens — cwd filter, step name normalization, order/campaign ID scoping |
