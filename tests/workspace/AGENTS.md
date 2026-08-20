# workspace/

Workspace cleanup, clone lifecycle, session skills, and worktree tests.

`_helpers.py` centralizes shared test constants, including `_CODEX_CAPABILITIES`.

## Architecture Notes

`conftest.py` provides shared fixtures for workspace tests. The `test_clone_*.py` files are split by concern from the original test_clone.py: core clone_repo behavior, push-to-remote, remote resolution/probing, and detect helpers. The `test_session_skills_*.py` files are split by concern across six files testing different aspects of the `session_skills` module.

The `test_project_local_overrides_*.py` files are split by concern: pure detection (`detection.py`), resolver behavior (`resolution.py`), and identity projection binding (`identity_projection.py`). The `test_skill_capabilities_cache.py` and `test_skill_capabilities_concurrency.py` files separate single-process correctness from concurrent + failure-recovery behavior.
