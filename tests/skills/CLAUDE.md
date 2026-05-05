# skills/

Skill SKILL.md content compliance, placeholder contracts, and verdict guard tests.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `conftest.py` | Skill test fixtures |
| `test_analyze_prs_contracts.py` | Contract tests for analyze-prs SKILL.md batch branch naming convention |
| `test_arch_lens_context_path.py` | Tests that all arch-lens skills have ## Arguments section and context_path handling |
| `test_audit_arch_preflight_contracts.py` | Audit-arch skill preflight contract tests |
| `test_audit_arch_selfvalidation_contracts.py` | Audit-arch skill self-validation contract tests |
| `test_audit_review_decisions_contracts.py` | Contract tests for the audit-review-decisions skill SKILL.md |
| `test_conflict_resolution_guards.py` | Structural guards for conflict resolution safeguards in SKILL.md files |
| `test_deletion_regression_guards.py` | Structural guards for deletion regression detection in merge-pr and review-pr skills |
| `test_dry_walkthrough_contracts.py` | Structural contracts for the dry-walkthrough historical regression check step |
| `test_investigate_contracts.py` | Structural contracts for the investigate historical recurrence check step |
| `test_investigate_deep_mode_contracts.py` | Structural contracts for the investigate deep analysis mode |
| `test_investigate_design_intent_contracts.py` | Contract tests for Design Intent Analysis requirements in the investigate skill |
| `test_isolation_guidance_contracts.py` | Contract tests verifying shared mutable state isolation guidance exists in pipeline skills |
| `test_make_campaign_compliance.py` | Compliance tests for the make-campaign skill: classification, placeholder hygiene, contract registration |
| `test_merge_pr_ci_gate.py` | Guards for the gh-pr-merge CI gate introduced in Part B of issue #289 |
| `test_open_integration_pr_domain_analysis.py` | Open integration PR domain analysis tests |
| `test_open_research_pr_decomposition.py` | Structural guards: decomposed research-PR skills must not invoke sub-skills via the Skill tool |
| `test_phase2_skills.py` | Phase 2 tests: open-kitchen and close-kitchen SKILL.md files |
| `test_plan_experiment_schema_contracts.py` | Contract tests: plan-experiment YAML frontmatter schema and revision_guidance argument |
| `test_planner_extract_domain.py` | Planner extract domain tests |
| `test_planner_skill_contracts.py` | Planner skill contract tests |
| `test_project_local_audit_skill_content.py` | Tests for project-local audit skill content |
| `test_promote_split_contracts.py` | Contract tests verifying the promote-to-main / review-promotion split |
| `test_resolve_claims_review_guards.py` | Behavioral guards for resolve-claims-review/SKILL.md |
| `test_resolve_design_review_contracts.py` | Structural guards for resolve-design-review SKILL.md |
| `test_resolve_failures_ci_aware.py` | Contract guards for resolve-failures CI-awareness: verdict decision tree |
| `test_resolve_failures_guards.py` | Guards for resolve-failures SKILL.md: polling-cascade and output-bloat fixes |
| `test_resolve_research_review_guards.py` | Behavioral guards for resolve-research-review/SKILL.md |
| `test_resolve_review_diff_context_consumption.py` | Guards: resolve-review loads and uses diff_context handoff file from review-pr |
| `test_resolve_review_diff_hunk_preference.py` | Guards: resolve-review Step 3.5 prefers diff_hunk over source file reads |
| `test_resolve_review_duplicate_comments.py` | Resolve review duplicate comments guard |
| `test_resolve_review_intent_validation.py` | Structural guards for resolve-review intent-validation phase |
| `test_resolve_review_severity_and_reject_resolution.py` | Resolve review severity and reject resolution tests |
| `test_resolve_review_thread_resolution.py` | Resolve review thread resolution tests |
| `test_resolve_review_token_optimizations.py` | Structural guards for resolve-review SKILL.md token-optimization edits |
| `test_retired_skill_names.py` | Convention guard: RETIRED_SKILL_NAMES entries must not have live directories, must be lowercase, and runtime raises on retired skill |
| `test_review_design_contracts.py` | Contract tests for review-design SKILL.md behavioral encoding |
| `test_review_design_guards.py` | Guard tests for review-design SKILL.md — data_acquisition dimension |
| `test_review_flag_marker.py` | Tests for the REVIEW-FLAG HTML comment marker used in resolve-review replies |
| `test_review_pr_adaptive_dispatch_guards.py` | Behavioral guard tests for review-pr adaptive subagent dispatch |
| `test_review_pr_diff_context_handoff.py` | Guards: review-pr writes diff_context handoff file in Step 8 before verdict emission |
| `test_review_pr_inline_comment_guards.py` | Structural guards for review-pr/SKILL.md posting mechanics |
| `test_review_pr_prior_thread_awareness.py` | Behavioral guard tests for review-pr/SKILL.md prior-thread awareness (T_RPA1–T_RPA7) |
| `test_review_pr_verdict_guards.py` | Behavioral guard tests for review-pr/SKILL.md verdict logic |
| `test_review_research_pr_guards.py` | Behavioral guards for review-research-pr/SKILL.md |
| `test_skill_body_cleanliness.py` | Assert that no SKILL.md body references %%ORDER_UP%% |
| `test_skill_compliance.py` | SKILL.md compliance tests: structural invariants for skill composition safety |
| `test_skill_genericization.py` | Verify skill SKILL.md files contain no project-specific AutoSkillit internals |
| `test_skill_output_compliance.py` | Tests that all SKILL.md output path instructions use HHMMSS-precision timestamps |
| `test_skill_placeholder_contracts.py` | Validate that no SKILL.md bash code block uses an undefined {placeholder} token |
| `test_skill_preambles.py` | Tests that critical SKILL.md preamble patterns are present (FRICT-1B-1, FRICT-1C-2) |
| `test_skill_tool_syntax_contracts.py` | Validates that SKILL.md bash blocks do not contain grep BRE \\| alternation patterns |
| `test_sous_chef_deferred_escalation.py` | Contract tests for sous-chef deferred issue escalation (T6/T7) |
| `test_tier1_no_temp_reference.py` | Tier 1 SKILL.md files must not reference temp at all |
| `test_tier2_3_no_literal_temp.py` | Tier 2/3 SKILL.md files must use {{AUTOSKILLIT_TEMP}}, never the literal |
| `test_troubleshoot_experiment_contracts.py` | Troubleshoot experiment skill discoverability test |
| `test_validate_audit_contracts.py` | Contract tests for the validate-audit skill SKILL.md |
| `test_validate_test_audit_contracts.py` | Contract tests for the validate-test-audit skill SKILL.md |
| `test_vis_lens_structural.py` | Structural assertions for P0 vis-lens skills |

## Architecture Notes

`conftest.py` provides skill test fixtures. Tests in this directory target SKILL.md content for correctness and compliance — not the skill loader infrastructure (which is in `tests/workspace/`).
