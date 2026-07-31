# workspace/

Workspace cleanup, clone lifecycle, session skills, and worktree tests.

`_helpers.py` centralizes shared test constants, including `_CODEX_CAPABILITIES`.

## Architecture Notes

`conftest.py` provides shared fixtures for workspace tests. The `test_clone_*.py` files are split by concern from the original test_clone.py: core clone_repo behavior, push_to_remote, remote resolution/probing, and detect helpers. The `test_session_skills_*.py` files are split by concern across six files testing different aspects of the `session_skills` module.
