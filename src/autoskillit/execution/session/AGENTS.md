# session/

Session result processing — parse, validate content, compute retry, adjudicate outcome.

## Architecture Notes

The sub-modules form a pipeline: parse -> check content -> compute retry -> compute outcome. `_exit_classification.py` provides a parallel infrastructure classification (context exhaustion, API error, process kill) used by `_headless_result.py` for resume routing. When Channel B is the sole confirmation source, `_compute_success` applies a provenance bypass — the session JSONL marker is treated as authoritative proof of success without requiring stdout content.
