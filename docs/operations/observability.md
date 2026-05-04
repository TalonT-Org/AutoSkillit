# Observability

What AutoSkillit records about a recipe run, where it lives, and how to
query it.

## In-memory accumulators

`pipeline/context.py:ToolContext` carries four accumulators that every tool
handler appends to:

- `pipeline/tokens.py:DefaultTokenLog` — per-step token usage extracted from
  the headless session output via `execution/session.py:extract_token_usage`.
- `pipeline/timings.py:DefaultTimingLog` — per-step wall-clock duration.
- `pipeline/mcp_response.py:DefaultMcpResponseLog` — per-tool response size
  in bytes (used to detect runaway tool output).
- `pipeline/audit.py:DefaultAuditLog` — failure records keyed by step id.

The accumulators stay in memory for the lifetime of the orchestrator session
and are written out at the end via `write_telemetry_files`.

## TelemetryFormatter

`pipeline/telemetry_fmt.py:TelemetryFormatter` is the single source of truth
for the human-readable token and timing tables. The MCP tool
`get_token_summary` delegates to it directly. The `token_summary_hook.py`
PostToolUse hook maintains stdlib-only parallel implementations of
`_format_efficiency_table` and `_format_table` (cannot import from
`autoskillit.*` — enforced by `tests/arch/test_ast_rules.py`). Output
equivalence between the canonical formatter and the hook is enforced by
`test_efficiency_table_equivalence` and `test_token_table_equivalence` in
`tests/infra/test_token_summary_core.py`. The canonical formatter derives
markdown headers from `_EFFICIENCY_COLUMNS` / `_TOKEN_COLUMNS` via
label-mapping dicts rather than hardcoding header strings.

## Mid-run accessors

The orchestrator can read accumulators mid-run via the status MCP tools:

- `get_token_summary` — current per-step token totals
- `get_timing_summary` — current per-step wall-clock totals
- `get_quota_events` — quota throttle events from `quota_check.py`
- `get_pipeline_report` — composite snapshot of all accumulators
- `read_db` — read-only SQLite query against the audit log

## consecutive_failures

Each step records its `consecutive_failures` counter so the orchestrator can
escalate to a human after the configured threshold. The counter resets on the
first success.

## Linux process tracing

`execution/linux_tracing.py` reads `/proc` and uses `psutil` to capture
periodic snapshots of every descendant of a headless Claude session: RSS, CPU
time, FDs, child PIDs, network connections. Snapshots accumulate into
`ProcSnapshot` records and are written to disk per session.

## 7 anomaly rules

`execution/anomaly_detection.py` runs 7 post-hoc rules over the
`ProcSnapshot` series and flags any anomaly into `anomalies.jsonl`. The
rules cover RSS spikes, FD leaks, runaway child counts, network surges,
CPU starvation, hung-with-no-progress, and zombie accumulation.

## Session logs path resolution

`execution/session_log.py` writes diagnostics to:

- Linux: `~/.local/share/autoskillit/logs/`
- macOS: `~/Library/Application Support/autoskillit/logs/`
- Override: `linux_tracing.log_dir` in config

Per-session layout:

```
sessions/
  <session-uuid>/
    proc_trace.jsonl    # ProcSnapshot stream
    anomalies.jsonl     # detected anomalies
    raw_stdout.jsonl    # captured headless stdout
sessions.jsonl          # one summary line per session
```

Session directory names are **hyphen-separated**, never underscored — see the
hyphens-not-underscores invariant in
[../developer/diagnostics.md](../developer/diagnostics.md).

## sessions.jsonl queries

```bash
# Failed sessions
jq 'select(.success == false)' ~/.local/share/autoskillit/logs/sessions.jsonl

# Sessions with anomalies
jq 'select(.anomaly_count > 0)' ~/.local/share/autoskillit/logs/sessions.jsonl
```

## 500-directory retention

`execution/session_log.py` keeps the most recent 500 session directories and
prunes older ones at every new session start. `sessions.jsonl` is also rewritten
on each prune to remove entries for deleted session directories.

## Recording and replay

`execution/recording.py` provides `RecordingSubprocessRunner` (records every
subprocess invocation to disk) and `ReplayingSubprocessRunner` (replays a
prior recording for deterministic test runs). The replay machinery is built
on top of `api-simulator`. The 0.7.26 release rewrote the hot path in Rust /
PyO3 to remove the Python overhead from each captured event.

## read_db triple-locked design

`execution/db.py` exposes `read_db` as a strictly read-only interface to the
SQLite audit log. Three independent enforcement layers prevent any write:

1. The connection is opened with `?mode=ro` in the URI.
2. The cursor is wrapped in a guard that rejects any non-SELECT statement.
3. The MCP tool layer validates the SQL string against an allow-list before
   passing it to the cursor.
