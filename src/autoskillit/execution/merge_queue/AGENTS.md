# merge_queue/

GitHub merge queue watcher — polls PR state until merged, ejected, or timed out.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Main module: `DefaultMergeQueueWatcher` with single-GraphQL-round-trip polling |
| `_merge_queue_classifier.py` | `_classify_pr_state()` pure function: MERGED/EJECTED/STALLED/DROPPED classification |
| `_merge_queue_group_ci.py` | `_query_merge_group_ci()` and GraphQL mutation strings for auto-merge/enqueue |
| `_merge_queue_repo_state.py` | `fetch_repo_merge_state()`, push/merge-group trigger detection, rate-limit retry |

## Architecture Notes

The `random` module is explicitly re-exported from `__init__.py` to enable test monkeypatching of `merge_queue.random.uniform` for jitter control.
