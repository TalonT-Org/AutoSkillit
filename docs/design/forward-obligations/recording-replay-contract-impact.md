# Recording/Replay Contract Impact

| Field | Value |
|-------|-------|
| Status | Draft |
| Date | 2026-06-27 |

## Obligation

Recording/replay must remain backend-isolated. Cross-backend cassette replay is explicitly
a non-goal (see `docs/design/recording-replay-accepted-degradations.md`).

## BackendCapabilities Fields

- `replay_capable` and `record_capable` are defined on `BackendCapabilities`
  (`_type_backend.py:127–129`), both defaulting to `False`.
- `ClaudeCodeBackend`: both set to `True` via `CLAUDE_CODE_CAPABILITIES`
  (`_type_backend.py:249–250`).
- `CodexBackend`: both set to `False` (`codex.py:580–581`). Codex replay is handled via
  `CodexScenarioPlayer`.

## Format Detection

`_detect_backend_format()` in `recording.py:56–59` routes by the presence of
`*/codex_stdout.ndjson` inside the scenario directory:

```python
def _detect_backend_format(scenario_dir: Path) -> str:
    if any(scenario_dir.glob("*/codex_stdout.ndjson")):
        return "codex"
    return "claude"
```

This function is called by `build_replay_runner()` (`recording.py:494`) to select between
`CodexScenarioPlayer` and `make_scenario_player` (Claude player).

Recording dispatch in `RecordingSubprocessRunner.__call__()` (`recording.py:117–239`) uses
`capabilities.pty_required` to distinguish backends: PTY path for Claude, non-PTY path for
Codex (which writes `codex_stdout.ndjson` + `step_meta.json`).

Consumption gates at the server layer (`_factory.py:223–252`):
- `replay_capable` guards `build_replay_runner()` construction
- `record_capable` guards `RecordingSubprocessRunner` construction

## Contract Gaps

Adding a third backend (e.g., opencode-via-ACP) requires extending
`_detect_backend_format()` with a new sentinel file or NDJSON magic-byte check.

## Forward Obligation

Any new backend adding replay support must:

1. Declare `replay_capable=True` in its `BackendCapabilities`.
2. Provide a concrete `ScenarioPlayer` subclass.
3. Register a format-detection branch in `_detect_backend_format()` with a unique sentinel
   file.
4. Add a `make_scenario_player` factory dispatch for the new format.
5. If supporting recording: declare `record_capable=True` and implement the recording path
   in `RecordingSubprocessRunner.__call__()`.

## Key References

- `execution/recording.py` — `_detect_backend_format`, `RecordingSubprocessRunner`
- `execution/backends/codex_scenario_player.py` — `CodexScenarioPlayer`, `CodexStepRecord`
- `docs/design/recording-replay-accepted-degradations.md` — accepted degradations
- `core/types/_type_backend.py:127–129` — `replay_capable`, `record_capable` field definitions
- `server/_factory.py:223–252` — capability-gated construction
