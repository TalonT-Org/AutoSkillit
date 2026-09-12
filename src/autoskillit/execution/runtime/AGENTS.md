# runtime/

Launch resolution, headless commands, clone guard, test runner, and read-only
database reader — moved out of `execution/` top level so `execution/` holds
≤1 Python file (plus `evidence_reader.py`, owned by #4664).

## Architecture Notes

The moved modules each retain their original contract:

- **`launch_resolution.py`** — `DefaultLaunchResolver` plus internal
  credential-environment helpers.
- **`commands.py`** — `ClaudeHeadlessCmd` only.
- **`clone_guard.py`** — `CloneGuardPolicy`, `CloneSnapshot`,
  `ContaminationReport`, `build_clone_guard_policy`,
  `check_and_revert_clone_contamination`, plus internal worktree helpers
  (not re-exported by `execution`'s or `execution.runtime`'s `__all__`).
- **`testing.py`** — `DefaultTestRunner`, `build_sanitized_env`,
  `check_test_passed`, `condense_test_output`, `parse_pytest_summary`.
- **`db.py`** — `DefaultDatabaseReader`, `execute_readonly_query`
  (re-exported alias of `_execute_readonly_query`). SQLite access is
  read-only with defense in depth (`_select_only_authorizer` enforces
  SELECT-only via authorizer callback).

Inter-peer coupling: none within the move set (verified).
