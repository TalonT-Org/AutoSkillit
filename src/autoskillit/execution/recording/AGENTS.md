# recording/

Record/replay tooling for headless sessions. Inclusion criterion: code that only runs while
a scenario is being recorded (`AUTOSKILLIT_RECORD_SESSION`) or replayed
(`REPLAY_SCENARIO_ENV` / `REPLAY_SCENARIO_DIR_ENV`). Production paths import this gateway
(`headless/__init__.py`, `server/_factory.py`) but never activate it.

## Architecture Notes

- **`recording.py`** — `RecordingSubprocessRunner`, `ReplayingSubprocessRunner`,
  `ScenarioReplayError`, `build_replay_runner`, plus the `RECORD_SCENARIO_*`,
  `REPLAY_SCENARIO_*`, and `SCENARIO_STEP_NAME_ENV` names. Depends on
  `backends.codex_scenario_player` and `process.DEFAULT_TETHER_CEILING_SECONDS`.
- **`_recording_skills.py`** — `restore_skill_snapshot`, `scan_skill_snapshots`,
  `snapshot_skill_dir`, plus internal skill-tree helpers. Depends only on `core`.

Inter-peer coupling (absolute imports): `recording.py` → `_recording_skills`. Nothing here
imports `evidence/` or `session_log/`.

## Cross-package edges

- `recording.py` → `backends.codex_scenario_player` (scenario player consumed by replay paths)
- `recording.py` → `process.DEFAULT_TETHER_CEILING_SECONDS` (shared tether ceiling constant)
- `recording.recording` → `recording._recording_skills` (intra-package; documented above)

## Registered identities keyed on this package's paths

- `tests/_retention_surface.py` — `_recording_skills.snapshot_skill_dir` is registered under
  `ACKNOWLEDGED_NON_RECLAIMERS` with reason `_SEPARATE_LIFECYCLE_REASON` (line-number keyed
  — edits above the registered function shift its key).
- `tests/arch/test_layer_enforcement.py`, `tests/arch/test_no_backend_name_bypass.py` —
  layer/name checks that name `recording` and `recording.recording` directly.
- `tests/infra/test_plugin_source_ratchets.py`,
  `tests/infra/test_schema_read_convention.py` — source-map and schema-read conventions
  that name this package path.
