# workspace/

Workspace cleanup, clone lifecycle, session skills, and worktree tests.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `_helpers.py` | Shared test constants (e.g., _CODEX_CAPABILITIES) for tests/workspace/ |
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
| `test_project_local_overrides.py` | Tests for backend-neutral project-local detection, effective resolution, and late rendering |
| `test_session_skills_allow_only_and_closure.py` | Tests for effective invocation closure and write-path contracts |
| `test_session_skills_provider.py` | Tests for unified projection and exact-catalog materialization |
| `test_session_skills_codex.py` | Tests for Codex-specific session skill layout, config file copying, and backend regression guard |
| `test_skill_format.py` | Unit tests for skill frontmatter validation functions |
| `test_skill_content_substitution.py` | Tests for SkillsDirectoryProvider.get_skill_content placeholder substitution |
| `test_session_skills_stale_path.py` | Tests for validate_session_exists() and cleanup_stale() structured logging |
| `test_skills.py` | Tests for skill resolution hierarchy |
| `test_worktree.py` | Worktree tests |

## Architecture Notes

`conftest.py` provides shared fixtures for workspace tests. The `test_clone_*.py` files are split by concern from the original test_clone.py: core clone_repo behavior, push_to_remote, remote resolution/probing, and detect helpers. The `test_session_skills_*.py` files are split by concern across six files testing different aspects of the `session_skills` module.
