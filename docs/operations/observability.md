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

## Codex cook startup traces

Codex interactive startup has a separate append-only trace. It is disabled by
default. Set `AUTOSKILLIT_CODEX_STARTUP_TRACE=1` on the outer `autoskillit
cook` invocation to enable it. Absence means disabled; every other present
value is a launch-blocking configuration error. Cook consumes the variable
before building the child, and backend, nested, and headless environments
scrub it.

### Path and schema

`StartupTrace` schema version 1 writes to:

```text
<default-log-dir>/codex-startup/<project-key>/<launch-id>.jsonl
```

`project-key` is the first 16 hexadecimal characters of SHA-256 over the
canonical project path. `launch-id` must be exactly 16 lowercase hexadecimal
characters. The writer containment-checks the path, refuses existing
symlinks, opens with no-follow append semantics, and fsyncs each record.

Every record contains `schema_version`, `record_type`, `launch_id`, and
`monotonic_seconds`. Attempt and stage records also contain `attempt` and the
backend-returned `view_id`. Record types and meanings are:

| Record | Meaning |
|---|---|
| `launch` | Final confirmation completed; this is the launch timing anchor |
| `attempt` | A fresh or reload attempt entered its durable backend context |
| `stage: spawn` | `Popen` returned successfully with the owned PID/PGID |
| `stage: state_ready` | The guarded Codex state query returned `complete` |
| `stage: first_output` | The PTY relayed its first non-empty output bytes |
| `stage: hook_review` | ANSI-normalized output semantically requested hook review |
| `summary` | Exactly-once terminal status, durations, budgets, and exceeded-budget names |

The terminal owner supplies statuses such as `success`, `error`,
`interrupted`, and `child_failed`. `trace_record_overflow` is emitted by the
writer when mandatory fields cannot fit. Readiness failures retain their
typed status (`unsupported_version`, `absent`, `locked`, `corrupt`,
`incomplete`, `schema_changed`, `timeout`, or `cancelled`) in failure
diagnostics and must never be reported as ready.

### Readiness guard

The adapter is version-mapped, not discovery-based. For exactly
`codex-cli 0.145.0`, it opens `<generated-home>/state_5.sqlite` with
`mode=ro`, then applies:

```sql
PRAGMA query_only = ON;
PRAGMA busy_timeout = 0;
PRAGMA table_info(backfill_state);
SELECT status FROM backfill_state WHERE id = 1;
```

The table must expose both `id` and `status`, and only the exact value
`complete` is ready. An unknown Codex version, absent or locked file, corrupt
database, missing/changed schema, incomplete row, timeout, or cancellation is
an explicit non-ready result. The enabled installed-Codex canary fails on an
unrecognized schema.

### Hard budgets and history diagnostics

The summary computes three absolute monotonic durations:

| Duration | Hard ceiling |
|---|---:|
| `confirmation_to_spawn` | 5 seconds |
| `spawn_to_hook_review` | 12 seconds |
| `total_startup` | 17 seconds |

`budget_exceeded` names every breached ceiling and `budgets_passed` is false
when any is breached. These are absolute gates. A favorable history-size
comparison or noisy measurement cannot excuse one.

Attempt diagnostics may include `history_file_count` and
`history_allocated_bytes`; compare these with the empty/small profile to
report history-size deltas. Performance investigations use schema-valid
rollouts and record:

- one warm-up followed by at least three retained measurements for
  small-history, many-file, and large-byte profiles;
- randomized or interleaved profile order;
- each bounded raw sample, environment metadata, Codex version, file count,
  allocated bytes, and observed stage endpoints;
- median plus median absolute deviation (MAD), or coefficient of variation;
- an explicit instability flag when dispersion exceeds the investigation's
  declared threshold.

With only three retained samples, deltas and dispersion are diagnostic
evidence, not independent pass/fail gates. Canary artifacts belong in that
test's unique `<project>/.autoskillit/temp/<launch-id>/` directory and are not
committed.

### Bounded transcript behavior

Each durable JSONL record is capped at 16 KiB. Only optional diagnostics may
be UTF-8 byte-truncated; oversized mandatory fields close the trace with
`trace_record_overflow`.

The PTY relay is byte-transparent: bytes written to the terminal are exactly
the bytes read from the master. It separately retains at most 64 KiB of raw
output and a 64 KiB ANSI-normalized matching window for first-output and hook
review detection. The bounded window is diagnostic state, not a durable full
transcript, and must not be used to reconstruct or log secrets.

### Queries

```bash
TRACE_ROOT="$HOME/.local/share/autoskillit/logs/codex-startup"

# All terminal summaries and their hard-budget result
find "$TRACE_ROOT" -name '*.jsonl' -type f -print0 |
  xargs -0 jq -c 'select(.record_type == "summary") |
    {launch_id, status, budgets_passed, budget_exceeded, durations_seconds}'

# Stage timeline for one launch
jq -c '{record_type, stage, attempt, view_id, monotonic_seconds}' \
  "$TRACE_ROOT/<project-key>/<launch-id>.jsonl"

# Attempts with populated-history diagnostics
find "$TRACE_ROOT" -name '*.jsonl' -type f -print0 |
  xargs -0 jq -c 'select(.record_type == "attempt" and
    (.diagnostics.history_file_count // 0) > 0)'
```

On macOS, substitute `~/Library/Application Support/autoskillit/logs` for the
Linux default log directory.

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
