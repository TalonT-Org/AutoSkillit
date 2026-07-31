# merge_queue/

GitHub merge queue watcher — polls PR state until merged, ejected, or timed out.

## Architecture Notes

The `random` module is explicitly re-exported from `__init__.py` to enable test monkeypatching of `merge_queue.random.uniform` for jitter control.
