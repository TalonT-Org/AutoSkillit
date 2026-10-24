<!-- autoskillit-recipe-hash: sha256:41e46ff502e0a7b7f35e7ef9f8a77c7d5a3f0a7f5859384b1380a7d3734b6227 -->
<!-- autoskillit-diagram-format: v7 -->
## merge-prs
Merge multiple PRs into an integration branch with conflict resolution and CI gates.

### Graph
clone → setup_remote → check_repo_ci_event
|
check_integration_exists → confirm_create_integration (optional)
|
fetch_merge_queue_data → analyze_prs → route_by_queue_mode
|
+-- [queue mode]:
|     enqueue → wait → advance → next PR
|     → resolve ejected conflicts on failure
|
+-- [integration mode]:
|     create_batch_branch → publish → check_pr_merge_loop
|     |
|     ┌────┤ FOR EACH PR:
|     │    merge_pr → plan → verify → implement → test
|     │    → merge → push → next_or_done
|     └────┘
|     |
|     audit_impl → remediate (optional)
|     |
|     open_integration_pr → ci_watch → review
|
+-- diagnose_ci → resolve_ci (on CI failure)
|
patch_token_summary (optional)
─────────────────────────────────────
done  "Complete."
escalate_stop  "Failed."
