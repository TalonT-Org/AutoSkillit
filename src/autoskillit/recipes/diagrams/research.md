<!-- autoskillit-recipe-hash: sha256:810be9842d1e34a236f2ce1fc60f534a1fb67e90aab0153f5102233387b304c9 -->
<!-- autoskillit-diagram-format: v7 -->

## research

scope
|
plan_experiment
|
review_design
|   +-- [plan_visualization] (optional)
|   +-- [resolve_design_review] (on STOP verdict)
|   x fail [-> escalate_stop]
|
stage_data
|
setup_environment
|
decompose_phases

+----+ FOR EACH PHASE:
|    |
|    plan_phase
|    |
|    implement_phase <-> [x fail -> troubleshoot_implement_failure]
|    |    x exhausted [-> run_experiment]
|
+----+

run_experiment <-> [adjust_experiment] (optional)
|    x fail [-> troubleshoot_run_failure]
|    x exhausted [-> ensure_results]
|
generate_report
|
test <-> [x fail -> fix_tests -> retest]
|
push_branch
|
prepare_research_pr
|
run_experiment_lenses
|
stage_bundle
|
route_pr_or_local

+--+ pr mode:
|    |
|    compose_research_pr
|    |    +-- [review_research_pr] (optional)
|    |    +-- [audit_claims] (optional)
|    |    +-- [resolve_research_review] (on changes_requested)
|    |    +-- [resolve_claims_review] (on changes_requested)
|    |    |
|    |    merge_escalations
|    |    |    +-- [re_run_experiment] (optional)
|    |    |    |    |
|    |    |    re_generate_report
|    |    |    |    x fail [-> re_push_research]
|    |    |    |
|    |    |    re_test <-> [x fail -> re_push_research]
|    |    |    |
|    |    re_push_research
|    |    |    x fail [-> begin_archival]
|    |    |
|    |    finalize_bundle_render
|    |    |    x fail [-> route_archive_or_export]
|    |    |
|    |    begin_archival
|    |    |    capture_experiment_branch
|    |    |    |    x fail [-> patch_token_summary]
|    |    |    |
|    |    |    create_artifact_branch
|    |    |    |    x fail [-> patch_token_summary]
|    |    |    |
|    |    |    open_artifact_pr
|    |    |    |    x fail [-> patch_token_summary]
|    |    |    |
|    |    |    tag_experiment_branch
|    |    |    |    x fail [-> patch_token_summary]
|    |    |    |
|    |    |    close_experiment_pr
|    |    |    |    x fail [-> patch_token_summary]
|    |    |
|    |    patch_token_summary
|    |
|    +-- [finalize_bundle] (local mode)
|    |    |
|         finalize_bundle_render
|         |    x fail [-> route_archive_or_export]
|         |
|         export_local_bundle
|         |    x fail [-> patch_token_summary]
|         |
|         patch_token_summary
