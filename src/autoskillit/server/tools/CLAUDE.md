# tools/

MCP `@mcp.tool()` handlers registered on import (14 tool modules).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Docstring-only — tools register via `@mcp.tool()` on import |
| `tools_kitchen.py` | `open_kitchen`, `close_kitchen` (gate lifecycle), `recipe://` MCP resource |
| `tools_ci.py` | `set_commit_status`, `check_repo_merge_state` |
| `tools_ci_watch.py` | `wait_for_ci`, `get_ci_status`, `_auto_trigger_ci` |
| `tools_ci_merge_queue.py` | `toggle_auto_merge`, `enqueue_pr`, `wait_for_merge_queue` |
| `tools_clone.py` | `clone_repo`, `remove_clone`, `push_to_remote`, `register_clone_status`, `batch_cleanup_clones`, `bootstrap_clone` |
| `tools_execution.py` | `run_cmd`, `run_python`, `run_skill`, `dispatch_food_truck` |
| `tools_git.py` | `merge_worktree`, `classify_fix`, `create_unique_branch`, `create_and_publish_branch`, `check_pr_mergeable` |
| `tools_github.py` | `fetch_github_issue`, `get_issue_title`, `report_bug` |
| `tools_issue_lifecycle.py` | `prepare_issue`, `enrich_issues`, `claim_issue`, `release_issue` |
| `tools_issue_composite.py` | `claim_and_resolve_issue` |
| `tools_pr_ops.py` | `get_pr_reviews`, `bulk_close_issues` |
| `tools_recipe.py` | `load_recipe`, `list_recipes`, `validate_recipe`, `migrate_recipe` |
| `tools_status.py` | `kitchen_status`, `get_pipeline_report`, `get_token_summary`, `get_timing_summary`, `analyze_tool_sequences`, `get_quota_events`, `write_telemetry_files`, `read_db` |
| `tools_workspace.py` | `test_check`, `reset_test_dir`, `reset_workspace` |

## Architecture Notes

Side-effect registration (same pattern as `recipe/rules/`). The `server/__init__.py` owns the `mcp` app object; tool modules import it from the server layer. All tools require `readOnlyHint: True` (see `server/CLAUDE.md`).
