# clone/

Clone/worktree lifecycle: repo cloning, run isolation, and deferred cleanup across
concurrent pipeline sessions.

## Architecture Notes

Clone paths live under `RUNS_DIR` (resolved by `_detect.py`). `_registry.py`
coordinates deferred cleanup across concurrent pipeline sessions using file-based
locking.
