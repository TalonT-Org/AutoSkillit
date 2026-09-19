"""execution/recording/ — record/replay scenario runners and skill-directory snapshots.

Only active under AUTOSKILLIT_RECORD_SESSION (record) or the REPLAY_SCENARIO_* env vars
(replay); production paths import this gateway but never activate it.
"""

from autoskillit.execution.recording._recording_skills import (
    restore_skill_snapshot,
    scan_skill_snapshots,
    snapshot_skill_dir,
)
from autoskillit.execution.recording.recording import (
    RECORD_SCENARIO_DIR_ENV,
    RECORD_SCENARIO_ENV,
    RECORD_SCENARIO_RECIPE_ENV,
    REPLAY_SCENARIO_DIR_ENV,
    REPLAY_SCENARIO_ENV,
    SCENARIO_STEP_NAME_ENV,
    RecordingSubprocessRunner,
    ReplayingSubprocessRunner,
    ScenarioReplayError,
    build_replay_runner,
)

__all__ = [
    "RecordingSubprocessRunner",
    "ReplayingSubprocessRunner",
    "ScenarioReplayError",
    "build_replay_runner",
    "RECORD_SCENARIO_ENV",
    "RECORD_SCENARIO_DIR_ENV",
    "RECORD_SCENARIO_RECIPE_ENV",
    "REPLAY_SCENARIO_ENV",
    "REPLAY_SCENARIO_DIR_ENV",
    "SCENARIO_STEP_NAME_ENV",
    "restore_skill_snapshot",
    "scan_skill_snapshots",
    "snapshot_skill_dir",
]
