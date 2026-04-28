<!-- autoskillit-recipe-hash: sha256:2d91a841f23823fad46d01fdfe82e768cfe970370fb37d8847f703d0579a242e -->
<!-- autoskillit-diagram-format: v7 -->
# implementation-groups

```mermaid
flowchart TD
    S0[clone]
    S1[capture_base_sha]
    S0 --> S1
    S2[get_issue_title]
    S1 --> S2
    S3[claim_issue]
    S2 --> S3
    S4[compute_branch]
    S3 --> S4
    S5[create_branch]
    S4 --> S5
    S6[push_merge_target]
    S5 --> S6
    S7[group]
    S6 --> S7
    S8[plan]
    S7 --> S8
    S9[review]
    S8 --> S9
    S10[verify]
    S9 --> S10
    S11[implement]
    S10 --> S11
    S12[retry_worktree]
    S11 --> S12
    S13[test]
    S12 --> S13
    S14[commit_guard]
    S13 --> S14
    S15[merge]
    S14 --> S15
    S16[push]
    S15 --> S16
    S17[fix]
    S16 --> S17
    S18[next_or_done]
    S17 --> S18
    S19[audit_impl]
    S18 --> S19
    S20[remediate]
    S19 --> S20
    S21[prepare_pr]
    S20 --> S21
    S22[run_arch_lenses]
    S21 --> S22
    S23[compose_pr]
    S22 --> S23
    S24[extract_pr_number]
    S23 --> S24
    S25[annotate_pr_diff]
    S24 --> S25
    S26[review_pr]
    S25 --> S26
    S27[resolve_review]
    S26 --> S27
    S28[re_push_review]
    S27 --> S28
    S29[check_review_loop]
    S28 --> S29
    S30[check_repo_ci_event]
    S29 --> S30
    S31[check_pr_state]
    S30 --> S31
    S32[ci_watch]
    S31 --> S32
    S33[handle_no_ci_runs]
    S32 --> S33
    S34[check_ci_loop]
    S33 --> S34
    S35[check_active_trigger_loop]
    S34 --> S35
    S36[trigger_ci_actively]
    S35 --> S36
    S37[escalate_stop_no_ci]
    S36 --> S37
    S38[diagnose_ci]
    S37 --> S38
    S39[resolve_ci]
    S38 --> S39
    S40[pre_resolve_rebase]
    S39 --> S40
    S41[re_push]
    S40 --> S41
    S42[check_repo_merge_state]
    S41 --> S42
    S43[route_queue_mode]
    S42 --> S43
    S44[enqueue_to_queue]
    S43 --> S44
    S45[verify_queue_enrollment]
    S44 --> S45
    S46[wait_for_queue]
    S45 --> S46
    S47[reenroll_stalled_pr]
    S46 --> S47
    S48[check_stall_loop]
    S47 --> S48
    S49[check_eject_limit]
    S48 --> S49
    S50[queue_ejected_fix]
    S49 --> S50
    S51[resolve_queue_merge_conflicts]
    S50 --> S51
    S52[re_push_queue_fix]
    S51 --> S52
    S53[ci_watch_post_queue_fix]
    S52 --> S53
    S54[reenter_merge_queue]
    S53 --> S54
    S55[reenter_merge_queue_cheap]
    S54 --> S55
    S56[direct_merge]
    S55 --> S56
    S57[wait_for_direct_merge]
    S56 --> S57
    S58[direct_merge_conflict_fix]
    S57 --> S58
    S59[resolve_direct_merge_conflicts]
    S58 --> S59
    S60[re_push_direct_fix]
    S59 --> S60
    S61[redirect_merge]
    S60 --> S61
    S62[immediate_merge]
    S61 --> S62
    S63[wait_for_immediate_merge]
    S62 --> S63
    S64[immediate_merge_conflict_fix]
    S63 --> S64
    S65[resolve_immediate_merge_conflicts]
    S64 --> S65
    S66[re_push_immediate_fix]
    S65 --> S66
    S67[remerge_immediate]
    S66 --> S67
    S68[register_clone_unconfirmed]
    S67 --> S68
    S69[detect_ci_conflict]
    S68 --> S69
    S70[ci_conflict_fix]
    S69 --> S70
    S71[release_issue_success]
    S70 --> S71
    S72[patch_token_summary]
    S71 --> S72
    S73[release_issue_failure]
    S72 --> S73
    S74[register_clone_success]
    S73 --> S74
    S75[register_clone_failure]
    S74 --> S75
    S76[done]
    S75 --> S76
    S77[escalate_stop]
    S76 --> S77
```
