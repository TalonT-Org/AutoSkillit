# execution/

IL-1 execution layer — headless Claude sessions, process lifecycle, CI/GitHub integration.
Sub-packages: headless/ (see headless/CLAUDE.md), process/ (see process/CLAUDE.md),
merge_queue/ (see merge_queue/CLAUDE.md), session/ (see session/CLAUDE.md),
backends/ (see backends/CLAUDE.md).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-exports `DefaultHeadlessExecutor`, `run_headless_core` |
| `commands.py` | `ClaudeInteractiveCmd`, `ClaudeHeadlessCmd` builders |
| `db.py` | Read-only SQLite with defence-in-depth |
| `diff_annotator.py` | Diff annotation + findings filter for review-pr |
| `linux_tracing.py` | `/proc` + psutil process tracing (Linux) |
| `anomaly_detection.py` | Post-hoc anomaly detection over snapshots |
| `session_log.py` | XDG-aware session diagnostics log writer (crash recovery moved to `_session_log_recovery.py`) |
| `_session_log_recovery.py` | `recover_crashed_sessions` — tmpfs orphan scanner for SIGKILL'd sessions |
| `recording.py` | Record/replay subprocess runners via api-simulator |
| `_recording_skills.py` | Skill dir snapshot/restore for record/replay sessions |
| `quota.py` | `QuotaStatus`, cache, `check_and_sleep_if_needed` |
| `ci.py` | GitHub Actions CI watcher (httpx, never raises) |
| `github.py` | GitHub issue fetcher |
| `remote_resolver.py` | Upstream > origin, clone-aware remote resolution (`REMOTE_PRECEDENCE` imported from `core/git_remote.py`) |
| `testing.py` | Pytest output parsing, pass/fail adjudication, output condensation |
| `clone_guard.py` | Clone contamination guard — detect and revert direct changes to clone CWD |
| `pr_analysis.py` | `extract_linked_issues`, `DOMAIN_PATHS`, `partition_files_by_domain` |

## Architecture Notes

`session_log.py` uses XDG base dir spec; log directory names use hyphens (never
underscores). `recording.py` and `_recording_skills.py` only activate when
`AUTOSKILLIT_RECORD_SESSION` is set; production paths never touch them.
