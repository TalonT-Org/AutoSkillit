# Design Record: Recording/Replay Accepted Degradations

**Status:** Accepted
**Date:** 2026-05-25
**Issue:** [#2918](https://github.com/TalonT-Org/AutoSkillit/issues/2918)

## Overview

This document records two accepted degradations in the P8 multi-backend
recording/replay architecture. Both are intentional design decisions, not bugs.

## OLD CASSETTE FORMAT INCOMPATIBILITY

Pre-P8 Claude PTY cassettes use a `stdout.jsonl` file inside a directory written
by `api_simulator.claude.ScenarioRecorder.record_step()`. The P8 Codex recording
path writes a different format: `codex_stdout.ndjson` plus `step_meta.json`,
written by `RecordingSubprocessRunner._record_non_pty_session()`.

**Consequence:** A pre-P8 Claude PTY cassette causes a parse error when used as a
Codex replay target. `_detect_backend_format()` in `recording.py` detects format
by the presence of `*/codex_stdout.ndjson` — a Claude cassette directory lacking
this file routes to `make_scenario_player` (the Claude replay path). Attempting to
force a Claude cassette through `CodexScenarioPlayer` fails because the expected
`codex_stdout.ndjson` file does not exist.

**Acceptability:** This is expected and acceptable. The two backends have
fundamentally different session models (PTY-captured JSONL vs NDJSON subprocess
stdout). Cross-backend cassette replay was never a design goal. The format
detection gate (`_detect_backend_format`) ensures each cassette routes to the
correct player.

**Key references:**
- `src/autoskillit/execution/recording.py` — `_detect_backend_format()` (format gate), `RecordingSubprocessRunner` (dispatch paths 1-3)
- `src/autoskillit/execution/backends/codex_scenario_player.py` — `CodexScenarioPlayer`, `CodexStepRecord`

## CLAUDE CASSETTE FORMAT PRESERVED

`ScenarioRecorder.record_step()` (from the external `api_simulator.claude` package)
still owns Claude cassette capture and the `pty_mode=True` guard remains the
backend boundary. AutoSkillit now wraps the recorded command in a private
supervisor because the external recorder waits only its direct PTY child and
does not expose process identity or descendant-cleanup evidence. The supervisor
inherits the recorder PTY, starts the real command in a new session, and emits a
typed receipt from AutoSkillit's identity-validating cleanup helper.

**Consequence:** Claude sessions keep the same `stdout.jsonl` cassette format and
replay compatibility. The cassette's `input.json` is restored to the original
command rather than the internal supervisor command. A recording result now
contains the real command PID and verified `CleanupOutcome`; cancellation writes
an invocation-scoped cooperative stop request and waits for the recorder to reap
the supervisor.

**Acceptability:** The wrapper changes process ownership, not cassette schema.
It closes the universal managed-cleanup contract while preserving the backend
format split: Claude sessions take the supervised PTY branch and Codex sessions
take the non-PTY managed-runner branch.

**Key references:**
- `src/autoskillit/execution/recording.py` — `RecordingSubprocessRunner.__call__()` line 132 (`if pty_mode:`)
- `src/autoskillit/execution/headless/_headless_helpers.py` — `_resolve_pty_mode()` (reads `backend.capabilities.pty_required`)

## References

- `src/autoskillit/execution/recording.py` — Record/replay subprocess runners
- `src/autoskillit/execution/backends/codex_scenario_player.py` — Codex replay player
- `src/autoskillit/execution/headless/_headless_helpers.py` — PTY mode resolution
- `src/autoskillit/core/types/_type_subprocess.py` — `SubprocessRunner` protocol (`pty_mode` parameter)
