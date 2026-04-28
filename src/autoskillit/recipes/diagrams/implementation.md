<!-- autoskillit-recipe-hash: sha256:31cc4041bae3dd8dfcbc7ab8518ba33c8aeeab99515f1181a273bd8812ae0e90 -->
<!-- autoskillit-diagram-format: v7 -->
# implementation

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
    S7[plan]
    S6 --> S7
    S8[review]
    S7 --> S8
    S9[verify]
    S8 --> S9
    S10[implement]
    S9 --> S10
    S11[retry_worktree]
    S10 --> S11
    S12[test]
    S11 --> S12
    S13[commit_guard]
    S12 --> S13
    S14[merge]
    S13 --> S14
    S15[push]
    S14 --> S15
    S16[fix]
    S15 --> S16
    S17[next_or_done]
    S16 --> S17
    S18[audit_impl]
    S17 --> S18
    S19[remediate]
    S18 --> S19
    S20[prepare_pr]
    S19 --> S20
    S21[run_arch_lenses]
    S20 --> S21
    S22[compose_pr]
    S21 --> S22
    S23[extract_pr_number]
    S22 --> S23
    S24[annotate_pr_diff]
    S23 --> S24
    S25[review_pr]
    S24 --> S25
    S26[resolve_review]
    S25 --> S26
    S27[re_push_review]
    S26 --> S27
    S28[check_review_loop]
    S27 --> S28
    S29[check_repo_ci_event]
    S28 --> S29
    S30[check_pr_state]
    S29 --> S30
    S31[ci_watch]
    S30 --> S31
    S32[handle_no_ci_runs]
    S31 --> S32
    S33[check_ci_loop]
    S32 --> S33
    S34[check_active_trigger_loop]
    S33 --> S34
    S35[trigger_ci_actively]
    S34 --> S35
    S36[escalate_stop_no_ci]
    S35 --> S36
    S37[check_repo_merge_state]
    S36 --> S37
    S38[route_queue_mode]
    S37 --> S38
    S39[enqueue_to_queue]
    S38 --> S39
    S40[verify_queue_enrollment]
    S39 --> S40
    S41[wait_for_queue]
    S40 --> S41
    S42[reenroll_stalled_pr]
    S41 --> S42
    S43[check_stall_loop]
    S42 --> S43
    S44[check_eject_limit]
    S43 --> S44
    S45[queue_ejected_fix]
    S44 --> S45
    S46[resolve_queue_merge_conflicts]
    S45 --> S46
    S47[re_push_queue_fix]
    S46 --> S47
    S48[ci_watch_post_queue_fix]
    S47 --> S48
    S49[reenter_merge_queue]
    S48 --> S49
    S50[reenter_merge_queue_cheap]
    S49 --> S50
    S51[direct_merge]
    S50 --> S51
    S52[wait_for_direct_merge]
    S51 --> S52
    S53[direct_merge_conflict_fix]
    S52 --> S53
    S54[resolve_direct_merge_conflicts]
    S53 --> S54
    S55[re_push_direct_fix]
    S54 --> S55
    S56[redirect_merge]
    S55 --> S56
    S57[immediate_merge]
    S56 --> S57
    S58[wait_for_immediate_merge]
    S57 --> S58
    S59[immediate_merge_conflict_fix]
    S58 --> S59
    S60[resolve_immediate_merge_conflicts]
    S59 --> S60
    S61[re_push_immediate_fix]
    S60 --> S61
    S62[remerge_immediate]
    S61 --> S62
    S63[diagnose_ci]
    S62 --> S63
    S64[resolve_ci]
    S63 --> S64
    S65[pre_resolve_rebase]
    S64 --> S65
    S66[re_push]
    S65 --> S66
    S67[detect_ci_conflict]
    S66 --> S67
    S68[ci_conflict_fix]
    S67 --> S68
    S69[release_issue_success]
    S68 --> S69
    S70[register_clone_unconfirmed]
    S69 --> S70
    S71[release_issue_failure]
    S70 --> S71
    S72[register_clone_success]
    S71 --> S72
    S73[register_clone_failure]
    S72 --> S73
    S74[done]
    S73 --> S74
    S75[escalate_stop]
    S74 --> S75
```
