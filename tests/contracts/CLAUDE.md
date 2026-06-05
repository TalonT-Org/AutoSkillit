# contracts/

Protocol satisfaction, package gateway, and skill contract compliance tests.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `_anti_confirm_helpers.py` | Shared anti-confirmation regex for contract tests — mirrors production regex |
| `_anti_fab_helpers.py` | Shared anti-fabrication regex for contract tests — mirrors production guard pattern |
| `conftest.py` | Shared constants for contract tests — REFUSAL_SIGNALS |
| `test_activate_deps_completeness.py` | Contracts: SKILL.md activate_deps must cover invoked Skill tool calls |
| `test_advisory_coverage.py` | Contracts: SKILL_FILE_ADVISORY_MAP advisory hook coverage |
| `test_analyze_pipeline_health_contracts.py` | Contract tests for the analyze-pipeline-health skill — output patterns and delimiter |
| `test_api_surface_alignment.py` | REQ-C8-01 / C2-01: API surface alignment tests |
| `test_apply_review_dimensions_contracts.py` | Contract tests for apply-review-dimensions SKILL.md behavioral encoding — L1 gate, ADDRESSABLE/STRUCTURAL, subagent inputs, red-team, output tokens, findings manifest JSON schema |
| `test_backend_protocol.py` | Protocol conformance for CodingAgentBackend, StreamParser, ResultParser, and ClaudeCodeBackend |
| `test_backend_compliance.py` | Return-type and protocol conformance for all BACKEND_REGISTRY entries |
| `test_claim_issue_contracts.py` | Contract tests for claim_issue and release_issue MCP tools |
| `test_claude_code_interface_contracts.py` | Contract tests for Claude Code external interface conventions |
| `test_classify_experiment_type_contracts.py` | Contract tests for classify-experiment-type SKILL.md — registry-based classification, secondary modifiers, silent-type detection, output tokens, and skill_contracts.yaml registration |
| `test_collapse_issues_contracts.py` | Contract tests for the collapse-issues skill SKILL.md |
| `test_callable_skill_parity.py` | Cross-check: recipe-dispatched skills must have contract tests |
| `test_campaign_prompt_accuracy.py` | Contract: campaign prompt does not contain inaccurate semaphore language |
| `test_config_field_coverage.py` | REQ-CONFIG-001: every sub-config dataclass field must be referenced in from_dynaconf |
| `test_core_public_api_surface.py` | Validates that every symbol in autoskillit.core.__all__ is importable via the public gateway |
| `test_diagnose_ci_steps.py` | Contract tests for diagnose-ci SKILL.md step numbering and cross-reference integrity |
| `test_docstring_skill_prefix.py` | Contract: source files must not use /autoskillit: prefix for skills_extended skills |
| `test_environment_setup_design_contracts.py` | Contract tests verifying the environment-setup skill design doc completeness |
| `test_ephemeral_skill_namespace.py` | Contract: ephemeral SKILL.md bodies use the correct namespace for the session delivery channel |
| `test_enrich_issues_contracts.py` | Contract tests for the enrich-issues skill SKILL.md |
| `test_execution_map_contracts.py` | Contract tests for the build-execution-map skill SKILL.md |
| `test_exogenous_string_coupling.py` | Exogenous string coupling tests: orchestrator prompt triggers coupled to emitting module |
| `test_figure_spec_contracts.py` | Figure spec schema contracts: vis-lens producer fields ⊇ bundle-local-report consumer fields |
| `test_filter_env_var_coverage.py` | Tests that retry-worktree and audit-impl skills set filter env vars for test runs |
| `test_fleet_dispatch_bem_gate.py` | Contract: fleet dispatcher prompt contains BEM pre-step gate instructions |
| `test_generate_report_contracts.py` | Contract tests for generate-report SKILL.md — data provenance lifecycle |
| `test_github_ops.py` | Contract tests: GitHub operation semantics in SKILL.md files |
| `test_hook_bridge_coverage.py` | REQ-BRIDGE-001: quota guard hook config bridge must produce exactly the keys that resolve_quota_settings() reads |
| `test_implement_experiment_contracts.py` | Contract tests for implement-experiment SKILL.md — test infrastructure requirements |
| `test_input_type_semantic_correctness.py` | Cross-validate skill_contracts.yaml path input types against SKILL.md content |
| `test_instruction_surface.py` | Contract tests: every instruction surface must carry the pipeline tool restriction |
| `test_issue_body_discipline.py` | Cross-skill contract: no SKILL.md may append validation summaries to issue bodies |
| `test_issue_content_fidelity.py` | Cross-skill contract: content fidelity for issue body assembly |
| `test_issue_splitter_contracts.py` | Contract tests: issue-splitter skill correctness and triage-issues integration |
| `test_l1_packages.py` | Package export surface tests for the L1 sub-packages |
| `test_make_campaign_skill_contracts.py` | Contract tests: structural invariants for the make-campaign SKILL.md |
| `test_mermaid_palette_contracts.py` | Contract: any SKILL.md that generates mermaid diagrams must embed the canonical 9-class palette |
| `test_no_pagination_file_read.py` | Contract tests for no-pagination file read instruction in high-turn SKILL.md files |
| `test_package_gateways.py` | Tests for Package Gateway API (groupC) — REQ-GWAY-001 through REQ-GWAY-008 |
| `test_plan_experiment_contracts.py` | Contract tests for plan-experiment SKILL.md — data provenance lifecycle |
| `test_plan_visualization_contracts.py` | Contract tests: select-vis-lenses Tier B experiment-type canonical names match registry |
| `test_select_vis_lenses_contracts.py` | Contract tests: select-vis-lenses SKILL.md experiment type vocabulary and token emission |
| `test_pr_traceability_contracts.py` | Cross-skill contract tests for requirement traceability across PR lifecycle skills |
| `test_prepare_compose_pr_contracts.py` | Contract tests for prepare-pr and compose-pr skills |
| `test_prepare_research_pr_contracts.py` | Contract tests: prepare-research-pr SKILL.md experiment type vocabulary |
| `test_prepare_issue_contracts.py` | Contract tests for the prepare-issue SKILL.md |
| `test_process_issues_contracts.py` | Contract tests for the process-issues skill SKILL.md |
| `test_protocol_definitions.py` | Tests for Protocol definitions in core/_type_protocols_*.py shards (REQ-PROTO-007) |
| `test_protocol_satisfaction.py` | Tests for Protocol Contract Layer (GroupB) |
| `test_protocol_satisfaction_five.py` | Protocol satisfaction tests — Group Five (issue #1523) |
| `test_review_design_contracts.py` | Contract tests for review-design SKILL.md — orchestration dispatch, output tokens, on_context_limit, and retained Critical Constraints |
| `test_review_local_mode_contracts.py` | Contract tests for skill_contracts.yaml and SKILL.md validation for local review mode (mode=local) |
| `test_review_pr_diff_annotation.py` | C-RPR-1: Contract tests for review-pr diff annotation inputs |
| `test_run_experiment_contracts.py` | Contract tests for run-experiment SKILL.md — data provenance lifecycle |
| `test_scope_contracts.py` | Contract tests for the scope skill's SKILL.md template |
| `test_skill_contracts.py` | Contract tests: every delimiter-emitting skill must be registered in skill_contracts.yaml |
| `test_skillmd_output_structure.py` | Contract tests: SKILL.md Output section structural properties — IMPORTANT callout and code-fence prohibition |
| `test_skill_directive_descriptions.py` | Contract: headless recipe skills must use directive language in SKILL.md descriptions |
| `test_skill_transition_boundaries.py` | Contract tests for anti-confirmation instructions at SKILL.md transition boundaries |
| `test_skill_yaml_validation.py` | Contract: YAML workflow examples embedded in SKILL.md files must be valid recipes |
| `test_skill_format_compliance.py` | Universal contract: all bundled SKILL.md files have valid frontmatter |
| `test_sous_chef_parameter_forwarding.py` | Architectural contract: sous-chef SKILL.md must instruct the LLM to forward recipe step parameters (output_dir, stale_threshold, idle_output_timeout, step_provider) to run_skill |
| `test_sous_chef_quota_protocol.py` | Contract test: sous-chef SKILL.md must contain QUOTA WAIT PROTOCOL section |
| `test_sous_chef_routing.py` | Contract tests for the CONTEXT LIMIT ROUTING section in sous-chef SKILL.md |
| `test_sous_chef_scheduling.py` | Contract tests for the PARALLEL STEP SCHEDULING section in sous-chef SKILL.md |
| `test_stage_data_contracts.py` | Contract tests for stage-data SKILL.md — pre-flight resource feasibility gate |
| `test_sub_skill_refusal_contracts.py` | Cross-skill contract: every SKILL.md that invokes sub-skills must contain explicit refusal handling language |
| `test_synthesize_vis_plan_contracts.py` | Contract tests: synthesize-vis-plan SKILL.md structural and content invariants |
| `test_phoropter_null_synthesis_contracts.py` | Contract tests: phoropter-null-synthesis SKILL.md structural and content invariants |
| `test_phoropter_priority_synthesis_contracts.py` | Contract tests: phoropter-priority-synthesis SKILL.md structural and content invariants |
| `test_ticket_body_size_ceiling.py` | Cross-skill contract: issue-filing skills must document body size guard |
| `test_target_skill_invocability.py` | Contract: the target skill of a run_skill call must be invocable after session setup |
| `test_token_summary_contracts.py` | Structural contracts for the token summary pipeline |
| `test_tools_recipe_contracts.py` | Contract tests for tools_recipe.py MCP tool docstrings |
| `test_triage_contracts.py` | Contract tests for triage-issues --enrich flag and requirement enrichment behavior |
| `test_triage_issues_contracts.py` | Contract tests for triage-issues body-file safety (gh issue edit --body-file) |
| `test_version_consistency.py` | Cross-file version consistency: pyproject.toml, __init__.__version__, plugin.json, bundled recipe versions |
| `test_zero_change_circuit_breaker_contracts.py` | Contract tests: zero-change circuit breaker in implementation/remediation recipes |
| `test_fetch_issue_mock_contracts.py` | Contract test: all fetch_issue mock return values must include a 'state' field |
| `test_review_pr_severity_calibration.py` | Contract test: review-pr SKILL.md must contain severity calibration examples and grouping rule |
| `test_no_interpreter_writes_in_skills.py` | Contract: no SKILL.md may prescribe interpreter-mediated file writes (python3 -c / heredoc with write APIs) |
| `test_dry_walkthrough_transformation_extent.py` | Contract test: dry-walkthrough SKILL.md Step 2 must check transformation extent/scope |
| `test_dry_walkthrough_arch_catalog_reference.py` | Contract test: dry-walkthrough SKILL.md Step 4 must reference the Architectural Constraint Catalog |
| `test_download_data_contracts.py` | Contract tests for download-data SKILL.md — external dataset acquisition step |
| `test_source_attribution_contracts.py` | Cross-skill contract: source-attribution prohibition in dual-source skills |
| `test_project_local_skill_delivery_contract.py` | Symmetric delivery contract tests for project-local skill overrides |
| `test_translate_model_suffix_contract.py` | Contract: translate_model suffix preservation tied to backend capability flag |

## Architecture Notes

`conftest.py` provides `REFUSAL_SIGNALS` constants shared across many contract tests. `_anti_confirm_helpers.py` mirrors the production anti-confirmation regex for structural contract verification.
