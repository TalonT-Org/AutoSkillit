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

## CLAUDE SESSION RECORDING UNCHANGED

`ScenarioRecorder.record_step()` (from the external `api_simulator.claude` package)
and the `pty_mode=True` guard in `RecordingSubprocessRunner.__call__()` are
unmodified by P8. The Claude PTY recording path continues to function identically
to its pre-P8 behavior.

**Consequence:** No new capabilities are added to Claude session recording. Claude
sessions are recorded using the same PTY capture mechanism and produce the same
`stdout.jsonl` cassette format as before P8.

**Acceptability:** P8's scope is adding Codex backend support. Modifying the
proven Claude recording path would introduce unnecessary risk. The `pty_mode`
dispatch guard (`if pty_mode:` at `recording.py` line 132) cleanly separates the
two paths — Claude sessions take the PTY branch, Codex sessions fall through to
the non-PTY branch.

**Key references:**
- `src/autoskillit/execution/recording.py` — `RecordingSubprocessRunner.__call__()` line 132 (`if pty_mode:`)
- `src/autoskillit/execution/headless/_headless_helpers.py` — `_resolve_pty_mode()` (reads `backend.capabilities.pty_required`)

## References

- `src/autoskillit/execution/recording.py` — Record/replay subprocess runners
- `src/autoskillit/execution/backends/codex_scenario_player.py` — Codex replay player
- `src/autoskillit/execution/headless/_headless_helpers.py` — PTY mode resolution
- `src/autoskillit/core/types/_type_subprocess.py` — `SubprocessRunner` protocol (`pty_mode` parameter)
