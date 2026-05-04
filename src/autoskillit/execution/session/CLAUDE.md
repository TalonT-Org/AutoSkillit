# session/

Session result processing — parse, validate content, compute retry, adjudicate outcome.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Thin facade re-exporting all sub-module symbols |
| `_session_model.py` | `ClaudeSessionResult`, `ContentState`, `parse_session_result()`, `extract_token_usage()` |
| `_session_content.py` | Content validation: `_check_session_content()`, `_check_expected_patterns()` |
| `_retry_fsm.py` | Retry FSM: `_compute_retry()`, maps `(TerminationReason, CliSubtype)` to `RetryReason` |
| `_session_outcome.py` | High-level adjudication: `_compute_success()`, `_compute_outcome()` |

## Architecture Notes

The four sub-modules form a pipeline: parse -> check content -> compute retry -> compute outcome. When Channel B is the sole confirmation source, `_compute_success` applies a provenance bypass — the session JSONL marker is treated as authoritative proof of success without requiring stdout content.
