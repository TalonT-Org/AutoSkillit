# Observability

What AutoSkillit records about a recipe run, where it lives, and how to
query it.

## In-memory accumulators

`pipeline/context.py:ToolContext` carries accumulators that every tool handler
appends to:

- `pipeline/tokens.py:DefaultTokenLog` — per-step, per-backend/provider token
  usage selected from correlated native OTLP accounting or session parsing.
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
`autoskillit.*` — enforced by `tests/arch/test_hooks_are_stdlib_only.py`). Output
equivalence between the canonical formatter and the hook is enforced by
`test_efficiency_table_equivalence` and `test_token_table_equivalence` in
`tests/infra/test_token_summary_core.py`. The canonical formatter derives
markdown headers from `_EFFICIENCY_COLUMNS` / `_TOKEN_COLUMNS` via
label-mapping dicts rather than hardcoding header strings.

## Token availability and provider boundaries

Every accounting measure is a `{"state": ..., "value": ...}` record. The five
states refine the familiar absent/zero/no-recorded-value distinction:

- `measured` has a positive observed integer; `measured_zero` has an observed
  integer zero. Only these states render as numbers.
- `unavailable` means the backend/provider producer cannot report that measure.
  `unknown` means the measure should be reportable but this observation is
  missing or invalid. Neither state is a numeric zero.
- `not_applicable` means the measure has no semantic meaning for this scope.
  It is not an alias for either missing state.

Each token-bearing row names non-empty `backend` and `provider_used`. The latter
is the resolved serving provider/profile, not the agent backend or a guessed
upstream behind a gateway. Claude Code/Anthropic expects cache-write evidence;
Claude Code/MiniMax and current Codex parser output do not provide it. Unknown
source pairs classify missing measures as `unknown`. A field actually observed
as zero is `measured_zero`, even when the producer often omits that class.

`get_token_summary` returns `steps` and a `totals` list keyed by this complete
source pair. Token classes and ratios are never pooled across providers without
a named normalization contract. A ratio with an `unknown` required measure
renders `unknown`; `unavailable` and `not_applicable` scopes do not contribute
to its denominator, and an observed zero denominator renders `—`.

Token descriptor schema v4, turn sidecar schema v2, session index schema v17,
fleet campaign state v13, and campaign summary v2 retain these records. Legacy
numeric zero is decoded as `unknown` because older formats did not prove an
observation. `context_window_tokens` is positive capacity metadata, not a
measure; `context_fraction` exists only with observed cache-read usage and a
known capacity.

For Claude turns, the window is the CLI-reported context budget from
`modelUsage[<the session's requested model>].contextWindow`. The init record
identifies that request; otherwise the sole `modelUsage` key is used. Ambiguous,
invalid or conflicting evidence leaves the window unknown. The turn's `model`
is still the served name. This reports the CLI's budget, without independently
verifying the provider's maximum accepted input or correcting upstream reports.

## Mid-run accessors

The orchestrator can read accumulators mid-run via the status MCP tools:

- `get_token_summary` — current per-step rows and per-source-pair totals
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

## Anomaly rules

`execution/anomaly_detection.py` runs post-hoc rules over the
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
sessions.jsonl          # retained row per committed summary
sessions-archive.jsonl  # append-only rows evicted from sessions.jsonl
otlp.jsonl              # current native OTLP logs and metrics capture
otlp.jsonl.1            # single rotated generation
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

# Historical rows evicted from the live projection, tolerant of damaged records
jq -Rcn '[inputs | fromjson? | select(type == "object")] | unique_by(.dir_name)[]' \
  ~/.local/share/autoskillit/logs/sessions-archive.jsonl
```

The historical query buffers the archive before deduplicating its at-least-once
rows by `dir_name`. Archive-only history excludes the current live survivor
window. The archive retains index metadata, not pruned session artifact
directories, and operational consumers continue to read only `sessions.jsonl`.

## Rollout cohorts

Declare an evaluated change in `.autoskillit/config.yaml` before applying it:

```yaml
rollout:
  active:
    change_id: "prompt-cost-study"
    effective_at: "2026-11-01T18:00:00Z"
    scope:
      skills: ["make-plan"]
      backends: ["claude-code", "codex"]
      roles: ["planner"]
    expected_effect: "Reduce planning input tokens while preserving requirement coverage."
```

`change_id` identifies an unchanged declaration across the observation window.
`effective_at` is the intentional UTC cutover, not a date inferred from code or
configuration history. `scope` names affected skills, backends, and roles in a
queryable form; its lists describe the change and never govern admission or marker
attachment. `expected_effect` preserves the required, non-null, nonblank prediction
with every run so readers can compare intended effects with subsequent observations.
Empty scope lists are valid. Missing/null `active` is inactive; incomplete or
otherwise invalid active declarations are rejected before generic string coercion.

Declare the prediction and stable id before cutover, launch marked baseline work,
apply the change at the stated UTC boundary, and keep the same declaration active
while observing after runs. Clear `active` when the observation period ends. Use a
new `change_id` for a revised prediction, scope, or cutover. Code validates each
declaration and preserves each admitted run's snapshot; operators maintain stability
across independent runs. Recording the prediction beforehand distinguishes intended
effects from explanations developed after seeing outcomes. A pre-cutover marked row
is a baseline sample, but its logical start time does not prove when the prediction
was registered. Keep contemporaneous evidence if preregistration matters; neither
the current configuration nor the first after row establishes it.

Summary and index records store the same nested `rollout` object with normalized
UTC `effective_at`. Capture happens immediately before the first admitted launch;
all headless runs then carry it, including failures and runs outside the descriptive
scope. Same-run nudges and recovery keep that snapshot and logical start despite
later configuration changes. The index `timestamp` and summary `start_ts` identify
the admitted logical run. Operation, nudge-turn, and trace-enrollment times have
different purposes. A subsequent independent run captures the current declaration.
New unmarked rows contain null; older absent fields never imply cohort membership.

This query counts one explicit change id while streaming the live retained index.
Set `index_path` to `linux_tracing.log_dir / "sessions.jsonl"` if overriding the log
root. It skips reader non-row entries and absent/null markers, and raises a visible
error for malformed selected declarations or timestamps. It covers retained rows
and excludes archived history; memory and printed output do not grow with row count.

```python
from datetime import datetime, timedelta

from autoskillit.core import default_log_dir
from autoskillit.execution.session_log.session_index import iter_tolerant_session_index_lines

index_path = default_log_dir() / "sessions.jsonl"
change_id = "prompt-cost-study"
counts = {"before": 0, "after": 0}
for end_offset, row in iter_tolerant_session_index_lines(index_path):
    if row is None:
        continue
    rollout = row.get("rollout")
    if rollout is None:
        continue
    if not isinstance(rollout, dict):
        raise ValueError(f"Malformed rollout at byte {end_offset}")
    if rollout["change_id"] != change_id:
        continue
    if set(rollout) != {"change_id", "effective_at", "scope", "expected_effect"}:
        raise ValueError(f"Incomplete rollout at byte {end_offset}")
    prediction = rollout["expected_effect"]
    scope = rollout["scope"]
    if not isinstance(prediction, str) or not prediction.strip():
        raise ValueError(f"Invalid prediction at byte {end_offset}")
    if not isinstance(scope, dict) or set(scope) != {"skills", "backends", "roles"}:
        raise ValueError(f"Invalid scope at byte {end_offset}")
    if any(
        not isinstance(values, list)
        or any(not isinstance(value, str) or not value.strip() for value in values)
        for values in scope.values()
    ):
        raise ValueError(f"Invalid scope values at byte {end_offset}")
    started_at = datetime.fromisoformat(row["timestamp"])
    cutover_at = datetime.fromisoformat(rollout["effective_at"])
    if started_at.utcoffset() is None or cutover_at.utcoffset() != timedelta(0):
        raise ValueError(f"Expected aware run start and UTC cutover at byte {end_offset}")
    if started_at < cutover_at:
        counts["before"] += 1
    elif started_at >= cutover_at:
        counts["after"] += 1
print(counts)
```

A before/after delta is a prompt to investigate, not proof that the change caused it.
Work difficulty, repository state, and model versions can differ between cohorts.
Compare parsed instants, including fractional seconds: equality is after cutover.
Do not manufacture cohorts from content hashes, repository revisions, file mtimes,
or guessed dates. Existing aggregate trends do not consume rollout markers.

## Dated run analysis

The packaged deck files under `src/autoskillit/assets/deck/` are the rendering
template. A dated analysis preserves a separate snapshot of the report-index rows,
prepared measurements, and exact template bundle used for that run. Coverage is
limited to retained report-index sources; it does not reconstruct pruned session
history.

Use a fresh directory whose name starts with a UTC timestamp in
`YYYY-MM-DD_HHMMSS` form. The token makes repeated runs distinct:

```bash
RUN_DIR=".autoskillit/temp/analyze-run/2026-10-11_162824_demo"
autoskillit sessions analysis-prepare "$RUN_DIR" --change-id prompt-cost-study
```

Preparation refreshes and reads the report index, then freezes the five populations
(`sessions`, `requests`, `tools`, `subagents`, and `turns`) in `measurements.json`.
Its `payload.generated_at_ms` is the latest integer `time_ms` across those
populations, ignoring booleans and defaulting to zero when no valid time exists.
This fixed corpus anchor drives the report’s time windows; it is not the time the
analysis was created. The `analysis-finish` command records the actual finish time
in UTC milliseconds.

Ask the analysis skill to interpret the frozen packet:

```text
/autoskillit:analyze-run --change-id prompt-cost-study
```

The skill chooses a new dated directory under
`.autoskillit/temp/analyze-run/`, runs both preparation and finish, and returns a
`report_path` only after completion. Omit `--change-id` for an analysis with no
rollout cohort. To drive the two CLI steps directly, write a candidate to the
`.candidate-findings.json` path returned by preparation, then finish the same
directory:

```bash
autoskillit sessions analysis-finish "$RUN_DIR"
```

A completed directory contains `measurements.json`, `findings.json`, `report.html`,
and `analysis.json`; the temporary candidate is removed. The frozen packet retains
the HTML template, CSS, script bytes and order, so rendering uses the same CSP
script hashes. Verification rerenders from those frozen inputs and compares the
HTML bytes exactly. It does not compare an HTML hash that would be sensitive to the
finish timestamp.

An unchanged input fingerprint can reuse a verified completed interpretation and
its provenance. Existing completions with that fingerprint must verify and agree;
inconsistent candidates fail closed. Publication serializes under a finite
five-second lease and checks again inside the lease, so concurrent finishes
converge on the same verified candidate. Changed inputs produce a different
fingerprint and require a new interpretation; prior findings are not carried
forward.

With a `change_id`, the cohort includes only session rows bearing that exact id;
missing markers and other ids are excluded. The comparison parses each selected
row's original aware `timestamp`, including fractional seconds. A start before
`effective_at` is a baseline; a start exactly at cutover is after. `scope` describes
the intended change but does not filter rows. Malformed matched markers, conflicting
declarations, and selected rows without aware start times fail closed. If the id is
not present in retained runs, preparation errors rather than returning an empty
cohort. A pre-cutover row remains a baseline, not independent proof that the
prediction was preregistered.

Read sample counts per field: each token measure has its own `observed_sessions`,
`eligible_sessions`, and `state_counts`; failure share has its own sample and
unknown counts. `measured_zero` is an observed zero, while `unknown` is missing or
invalid evidence, not zero. The candidate at `candidate_path` must contain the
exact `snapshot_digest` returned by preparation, an `interpreter` object with
`role`, `model`, and `prompt`, and a nonempty `findings` list. Each finding has
`view_id`, nonblank plain-text `text`, and `evidence_ids`. Copy ids from the
packet's `evidence` map: each resolves to a specific table row, prepared measure,
or rollout group-and-metric comparison, and the referenced view must equal the
finding's `view_id`. An empty corpus still needs an explicit no-observations
finding; only then may its `evidence_ids` be empty. Numeric measures are computed
by Python and should be reported from the referenced record. Structural checks
validate the digest and evidence links, not whether the prose is true or whether
an observed difference is causal. The analyst must check each claim against its
referenced measurement.

Related rationale: [Megagon Labs’ discussion of auditable source-grounded
tables](https://megagon.ai/data-provenance-source-grounded-ai/) explains why
item-level evidence and separate scope checks help make structured claims
independently reviewable. This workflow applies that distinction to measurements
and interpretation prose.

## Native headless OTLP capture

Every headless executor invocation starts one best-effort OTLP/HTTP-JSON sink
on an ephemeral `127.0.0.1` port. Claude Code receives its native telemetry
activation plus log and metric exporters through environment variables. Codex
receives equivalent run-scoped `otel.exporter` and
`otel.metrics_exporter` command overrides. Interactive Codex launches and
persistent user or project configuration are unchanged.

The initial attempt, resume, contract nudge, and provider fallback within the
same executor invocation share that sink. The listener closes with the
invocation; a later orchestration retry creates a new listener in the same log
directory. Startup, request, queue, persistence, and shutdown failures remain
diagnostic-only and cannot change the headless result.

The sink accepts the native log, metric, and trace OTLP paths, but AutoSkillit
activates only the stable native log and metric exporters. Each `otlp.jsonl`
record wraps the PII-scrubbed vendor-native payload as `{"signal", "payload"}`
without normalizing its nested structure, rotating one 20 MiB generation to
`otlp.jsonl.1`. No collector, daemon, telemetry database, or additional package
is required.

The local sink derives model evidence from accepted Claude Code and Codex
`/v1/logs` batches. It also projects Claude Code `api_request` accounting
before queueing and retains one observation per `(session.id, request_id)`.
Duplicate requests are counted once; ambiguous or uncorrelated events are not
summed. The selected execution provider is retained instead of a vendor label.
Codex uses parser totals until native telemetry exposes a stable join — see
`docs/developer/diagnostics.md` for the full architectural rationale. The
captured vendor shapes and version metadata are in `tests/execution/fixtures/`.

After terminal execution, the best-effort lifecycle drains the sink, selects
correlated OTLP accounting when available, then flushes diagnostics. Sink
startup, queue, persistence, or close failure leaves parser or `unknown`
evidence; it never synthesizes zero or changes the headless outcome. The
reconciliation uses retained in-memory observations, not a raw `otlp.jsonl`
readback or a numeric cross-check. `sessions.jsonl` remains the
retained committed-session projection, not a general-purpose or raw telemetry
join, but it now includes this explicitly bounded session-keyed model
projection.

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
`mode=ro`. That mapping is pinned to upstream Codex commit
`ad65f016ed0c91992fb175fa881a373cc460dd2a`, specifically the
[`state` runtime](https://github.com/openai/codex/blob/ad65f016ed0c91992fb175fa881a373cc460dd2a/codex-rs/state/src/runtime.rs)
and
[`state` SQLite adapter](https://github.com/openai/codex/blob/ad65f016ed0c91992fb175fa881a373cc460dd2a/codex-rs/state/src/sqlite.rs).
Changing either the supported CLI version or this source revision requires a
new compatibility fixture. The adapter then applies:

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

## Retained session projection

`execution/session_log.py` targets the most recent 2,000 committed session
directories. Active-campaign protection can keep the survivor count above that
target. Under one exclusive lease, retention ranks only directories containing
`summary.json`, deletes expired unprotected sessions, and rewrites
`sessions.jsonl` to exactly the retained committed projection. `summary.json` is
the final per-session artifact and completion witness; the index `timestamp` is
still the session start time. Rows removed from the live projection are appended
to `sessions-archive.jsonl`; protected sessions and deletion failures remain live
and are not archived.

The child-terminal-reason snapshot (issue #4623,
`child-outcomes/{backend}/{parent_session_id}.json`) is exempt from this
eviction — it is the canonical source `summary.json`/`sessions.jsonl` project
from, not itself a retained-projection artifact, and it survives its own
parent's `sessions/` directory being pruned or never having existed (an
orphan or interactive-only parent). See [Child Terminal
Reasons](../developer/diagnostics.md#child-terminal-reasons) for the full
row shape and reason taxonomy.

## Candidate selection and continuation

`run_skill` writes a candidate-selection manifest before each pre-spawn route;
see [Execution-candidate manifests](../developer/diagnostics.md#execution-candidate-manifests).
When the result reports `candidate_exhausted: true`, inspect the ordered attempt
records and route through the recipe's rate-limit or failure handling. The
result does not mean an execution may be repeated: no configured alternative is
started after a worker has begun, including after an API error, deadline, or
rate limit.

A terminal rate-limited attempt may include a continuation recommendation. A
caller may resume only the returned `resume_session_id`, using the same verified
backend/provider binding, and only while the recommendation's reset delay and
remaining invocation deadline permit it. The reset delay is bounded to 60
seconds and continuation consumes the configured provider retry budget. If the
result lacks that explicit recommendation, do not resume or replay the original
call.

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
