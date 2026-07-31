# hooks/

Hook script behavior, registration, and bridge tests.

An autouse fixture provides project-root CWD isolation for every hook test.

## Architecture Notes

`test_token_summary_appender.py` contains only script-existence and source-quality checks (2 tests). All behavioral tests (early exit, PR editing, fail-open, efficiency table), unit tests (`_canonical`, `_humanize`, `_format_table`, `_unwrap_mcp_response`), and order_id isolation tests live in `tests/infra/test_token_summary_core.py`, `tests/infra/test_token_summary_filters.py`, and `tests/infra/test_token_summary_v1_compat.py`. Shared helpers are in `tests/infra/_token_summary_helpers.py`.
