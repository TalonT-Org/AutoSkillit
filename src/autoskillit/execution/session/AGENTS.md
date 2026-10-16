# session/

Session result processing — parse, validate content, compute retry, adjudicate outcome.

## Architecture Notes

The sub-modules form a pipeline: parse -> check content -> compute retry -> compute outcome. `_exit_classification.py` provides a parallel infrastructure classification (context exhaustion, API error, process kill) used by `_headless_result.py` for resume routing. Channel B is marker corroboration only; it cannot replace terminal or content evidence.
