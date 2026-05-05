# workspace/

Workspace cleanup, clone lifecycle, session skills, and worktree tests.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `conftest.py` | Shared fixtures for tests/workspace/ |
| `test_cleanup.py` | L1 unit tests for workspace/cleanup.py — CleanupResult and directory deletion |
| `test_clone_core.py` | Core clone_repo + remove_clone tests — setup, paths, error handling, origin contracts |
| `test_clone_detect.py` | detect_source_dir, detect_branch, detect_uncommitted_changes, classify_remote_url |
| `test_clone_push.py` | push_to_remote tests — E2E, mocked, protected branches, force-with-lease |
| `test_clone_remote.py` | Remote resolution — probe helpers, URL resolution, stale-clone regression |
| `test_clone_ci_contract.py` | Cross-boundary contract tests: clone isolation × CI/merge-queue resolution |
| `test_clone_registry.py` | Tests for autoskillit.workspace.clone_registry module |
| `test_clone_split.py` | Structural guard for clone test split |
| `test_clone_timeouts.py` | Static analysis: git network commands in clone.py must have timeouts |
| `test_constants.py` | Asserts that workspace directory name constants are exported from workspace/__init__ |
| `test_project_local_overrides.py` | Tests for project-local skill override detection and enforcement (T-OVR-001..011) |
| `test_session_skills_allow_only_and_closure.py` | Phase 2 tests: session_skills module — allow_only filter and compute_skill_closure |
| `test_session_skills_deps.py` | Phase 2 tests: session_skills module — activate_deps resolution |
| `test_session_skills_features.py` | Phase 2 tests: session_skills module — feature-gate skill filtering |
| `test_session_skills_filtering.py` | Phase 2 tests: session_skills module — subset/disabled-category and pack filtering |
| `test_session_skills_provider.py` | Phase 2 tests: session_skills module — provider and core manager |
| `test_skill_content_substitution.py` | Tests for SkillsDirectoryProvider.get_skill_content placeholder substitution |
| `test_skills.py` | Tests for skill resolution hierarchy |
| `test_worktree.py` | Worktree tests |

## Architecture Notes

`conftest.py` provides shared fixtures for workspace tests. The `test_clone_*.py` files are split by concern from the original test_clone.py: core clone_repo behavior, push_to_remote, remote resolution/probing, and detect helpers. The `test_session_skills_*.py` files are split by concern across five files testing different aspects of the `session_skills` module.
