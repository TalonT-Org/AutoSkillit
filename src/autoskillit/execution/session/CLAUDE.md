# session/

Session result processing — parse, validate content, compute retry, adjudicate outcome.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Thin facade re-exporting all sub-module symbols |
| `_exit_classification.py` | Infrastructure exit classification: `classify_infra_exit()`, `InfraExitCategory` detection from session + stderr |
| `_session_state.py` | Session state persistence for resume: `SessionState`, `persist_session_state()`, `read_session_state()`, `clear_session_state()` |
| `_session_model.py` | `ClaudeSessionResult`, `ContentState`, `parse_session_result()`, `extract_token_usage()` |
| `_session_content.py` | Content validation: `_check_session_content()`, `_check_expected_patterns()` |
| `_retry_fsm.py` | Retry FSM: `_compute_retry()`, maps `(TerminationReason, CliSubtype)` to `RetryReason` |
| `_session_outcome.py` | High-level adjudication: `_compute_success()`, `_compute_outcome()` |

## Architecture Notes

The sub-modules form a pipeline: parse -> check content -> compute retry -> compute outcome. `_exit_classification.py` provides a parallel infrastructure classification (context exhaustion, API error, process kill) used by `_headless_result.py` for resume routing. When Channel B is the sole confirmation source, `_compute_success` applies a provenance bypass — the session JSONL marker is treated as authoritative proof of success without requiring stdout content.
