# workspace/

IL-1 workspace management — clone lifecycle, worktrees, skill resolution.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-exports `DefaultCloneManager`, `SkillResolver`, `DefaultSessionSkillManager` |
| `cleanup.py` | `CleanupResult`, preserve list |
| `clone.py` | `clone_repo` + `push_to_remote` + `DefaultCloneManager` |
| `_clone_detect.py` | `detect_*` helpers + `RUNS_DIR` + `classify_remote_url` |
| `_clone_remote.py` | `CloneSourceResolution` + probe/isolate remotes |
| `session_skills.py` | Per-session ephemeral skill dirs; subset filtering |
| `clone_registry.py` | Shared file-based coordination for deferred cleanup |
| `skills.py` | `SkillResolver` — bundled skill listing |
| `worktree.py` | Git worktree creation and teardown helpers |

## Architecture Notes

Clone paths live under `RUNS_DIR` (resolved by `_clone_detect.py`). `clone_registry.py`
coordinates deferred cleanup across concurrent pipeline sessions using file-based locking.
`session_skills.py` builds per-session ephemeral copies of the bundled skill set so that
headless sessions can use a filtered subset without polluting the installed package.
