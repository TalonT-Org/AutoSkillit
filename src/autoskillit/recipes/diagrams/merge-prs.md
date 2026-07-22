<!-- autoskillit-recipe-hash: sha256:2b550118cf1a2f29a3c5f3a7274531de73787e2331ce54512281cd5ad75941d4 -->
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
|     create_batch_branch → publish
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
