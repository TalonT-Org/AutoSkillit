<!-- autoskillit-recipe-hash: sha256:0000000000000000000000000000000000000000000000000000000000000000 -->
<!-- autoskillit-diagram-format: v7 -->
## research
Design, execute, and report on experiments with visualization and archival.

### Graph
scope → plan_experiment → review_design (optional)
|
plan_visualization → revise_design (on rejection)
|
create_worktree → stage_data
|
┌────┤ FOR EACH PHASE:
│    plan_phase → implement_phase → next_phase_or_experiment
│    ✗ failure → troubleshoot → route
└────┘
|
run_experiment → adjust_experiment → ensure_results
|
generate_report → test
|
push_branch → prepare_research_pr → run_experiment_lenses
|
stage_bundle → compose_research_pr → review_research_pr
|
+-- audit_claims (optional)
|
+-- resolve_review → merge escalations (optional)
|
finalize_bundle → route_archive_or_export
|
+-- export_local_bundle
+-- begin_archival → capture_experiment_branch → open_artifact_pr
|
patch_token_summary (optional)
─────────────────────────────────────
research_complete  "Complete."
escalate_stop  "Failed."
