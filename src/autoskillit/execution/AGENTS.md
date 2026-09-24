# execution/

IL-1 execution layer — headless Claude sessions, process lifecycle, CI/GitHub integration.
Sub-packages: backends/ (see backends/AGENTS.md), evidence/ (see evidence/AGENTS.md),
github_ops/ (see github_ops/AGENTS.md), github_review/ (see github_review/AGENTS.md),
headless/ (see headless/AGENTS.md), merge_queue/ (see merge_queue/AGENTS.md),
process/ (see process/AGENTS.md), quota/, recording/ (see recording/AGENTS.md),
runtime/ (see runtime/AGENTS.md), session/ (see session/AGENTS.md),
session_log/ (see session_log/AGENTS.md).

SQLite access is read-only with defense in depth. The GitHub Actions CI watcher never
raises.

## Architecture Notes

`session_log/session_log.py` uses XDG base dir spec; log directory names use hyphens
(never underscores). `recording/recording.py` and `recording/_recording_skills.py`
activate only under `AUTOSKILLIT_RECORD_SESSION` or `REPLAY_SCENARIO_*`; production paths
never activate them.

`report_index.py` (storage, reader, and join) and `_report_index_rows.py` (schema and
derivation) are execution-root consumers of `evidence/report_walk.py` and `session_log/`,
re-exported through `execution/__init__.py`. They are outside `evidence/`, so this
dependency does not cycle through `session_log`.
