<!-- autoskillit-recipe-hash: sha256:1d5fac42c5bb63012f0407cb60737d61eca2eb680d3e461a56f588852875fabb -->
<!-- autoskillit-diagram-format: v7 -->

## research

scope
|
select_directions
|
plan_experiment
|
dial (phoropter: review-design)
|    +-- [silent_type_gate] (route — is_silent_type)
|    |    +-- [synthesize] (phoropter: review-design, when is_silent_type)
|    |    |
|    |    [apply] (phoropter: review-design, default after gate)
|    |    |    +-- [synthesize] (phoropter: review-design)
|    |    |    |
|    |    |    [revise_design] (on REVISE verdict)
|    |    |
|    |    [synthesize] (phoropter: review-design)
|    |    |    +-- [vis_dial] (on GO verdict)
|    |    |    +-- [revise_design] (on REVISE verdict)
|    |    |    +-- [resolve_design_review] (on STOP verdict)
|    |    |    x fail [-> create_worktree]
|    |    |
|    |    x fail [-> create_worktree]
|    |
|    x fail [-> create_worktree]
|
vis_dial
|
vis_apply
|
vis_synthesize
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
|    +-- finalize_bundle (local mode)
|    |    |
|    |    finalize_bundle_render
|    |    |    x fail [-> route_archive_or_export]
|    |    |
|    |    export_local_bundle
|    |    |    x fail [-> patch_token_summary]
|    |    |
|    |    patch_token_summary
