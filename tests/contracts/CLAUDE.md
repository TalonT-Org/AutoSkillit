# contracts/

Protocol satisfaction, package gateway, and skill contract compliance tests.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `_anti_confirm_helpers.py` | Shared anti-confirmation regex for contract tests — mirrors production regex |
| `conftest.py` | Shared constants for contract tests — REFUSAL_SIGNALS |
| `test_activate_deps_completeness.py` | Contracts: SKILL.md activate_deps must cover invoked Skill tool calls |
| `test_advisory_coverage.py` | Contracts: SKILL_FILE_ADVISORY_MAP advisory hook coverage |
| `test_api_surface_alignment.py` | REQ-C8-01 / C2-01: API surface alignment tests |
| `test_claim_issue_contracts.py` | Contract tests for claim_issue and release_issue MCP tools |
| `test_claude_code_interface_contracts.py` | Contract tests for Claude Code external interface conventions |
| `test_collapse_issues_contracts.py` | Contract tests for the collapse-issues skill SKILL.md |
| `test_config_field_coverage.py` | REQ-CONFIG-001: every sub-config dataclass field must be referenced in from_dynaconf |
| `test_core_public_api_surface.py` | Validates that every symbol in autoskillit.core.__all__ is importable via the public gateway |
| `test_docstring_skill_prefix.py` | Contract: source files must not use /autoskillit: prefix for skills_extended skills |
| `test_environment_setup_design_contracts.py` | Contract tests verifying the environment-setup skill design doc completeness |
| `test_enrich_issues_contracts.py` | Contract tests for the enrich-issues skill SKILL.md |
| `test_execution_map_contracts.py` | Contract tests for the build-execution-map skill SKILL.md |
| `test_exogenous_string_coupling.py` | Exogenous string coupling tests: orchestrator prompt triggers coupled to emitting module |
| `test_filter_env_var_coverage.py` | Tests that retry-worktree and audit-impl skills set filter env vars for test runs |
| `test_generate_report_contracts.py` | Contract tests for generate-report SKILL.md — data provenance lifecycle |
| `test_github_ops.py` | Contract tests: GitHub operation semantics in SKILL.md files |
| `test_hook_bridge_coverage.py` | REQ-BRIDGE-001: quota guard hook config bridge must produce exactly the keys that resolve_quota_settings() reads |
| `test_implement_experiment_contracts.py` | Contract tests for implement-experiment SKILL.md — test infrastructure requirements |
| `test_instruction_surface.py` | Contract tests: every instruction surface must carry the pipeline tool restriction |
| `test_issue_content_fidelity.py` | Cross-skill contract: content fidelity for issue body assembly |
| `test_issue_splitter_contracts.py` | Contract tests: issue-splitter skill correctness and triage-issues integration |
| `test_l1_packages.py` | Package export surface tests for the L1 sub-packages |
| `test_make_campaign_skill_contracts.py` | Contract tests: structural invariants for the make-campaign SKILL.md |
| `test_mermaid_palette_contracts.py` | Contract: any SKILL.md that generates mermaid diagrams must embed the canonical 9-class palette |
| `test_no_pagination_file_read.py` | Contract tests for no-pagination file read instruction in high-turn SKILL.md files |
| `test_package_gateways.py` | Tests for Package Gateway API (groupC) — REQ-GWAY-001 through REQ-GWAY-008 |
| `test_plan_experiment_contracts.py` | Contract tests for plan-experiment SKILL.md — data provenance lifecycle |
| `test_pr_traceability_contracts.py` | Cross-skill contract tests for requirement traceability across PR lifecycle skills |
| `test_prepare_compose_pr_contracts.py` | Contract tests for prepare-pr and compose-pr skills |
| `test_prepare_issue_contracts.py` | Contract tests for the prepare-issue SKILL.md |
| `test_process_issues_contracts.py` | Contract tests for the process-issues skill SKILL.md |
| `test_protocol_definitions.py` | Tests for Protocol definitions in core/_type_protocols_*.py shards (REQ-PROTO-007) |
| `test_protocol_satisfaction.py` | Tests for Protocol Contract Layer (GroupB) |
| `test_protocol_satisfaction_five.py` | Protocol satisfaction tests — Group Five (issue #1523) |
| `test_review_pr_diff_annotation.py` | C-RPR-1: Contract tests for review-pr diff annotation inputs |
| `test_run_experiment_contracts.py` | Contract tests for run-experiment SKILL.md — data provenance lifecycle |
| `test_scope_contracts.py` | Contract tests for the scope skill's SKILL.md template |
| `test_skill_contracts.py` | Contract tests: every delimiter-emitting skill must be registered in skill_contracts.yaml |
| `test_skill_directive_descriptions.py` | Contract: headless recipe skills must use directive language in SKILL.md descriptions |
| `test_skill_transition_boundaries.py` | Contract tests for anti-confirmation instructions at SKILL.md transition boundaries |
| `test_skill_yaml_validation.py` | Contract: YAML workflow examples embedded in SKILL.md files must be valid recipes |
| `test_sous_chef_quota_protocol.py` | Contract test: sous-chef SKILL.md must contain QUOTA WAIT PROTOCOL section |
| `test_sous_chef_routing.py` | Contract tests for the CONTEXT LIMIT ROUTING section in sous-chef SKILL.md |
| `test_sous_chef_scheduling.py` | Contract tests for the PARALLEL STEP SCHEDULING section in sous-chef SKILL.md |
| `test_stage_data_contracts.py` | Contract tests for stage-data SKILL.md — pre-flight resource feasibility gate |
| `test_sub_skill_refusal_contracts.py` | Cross-skill contract: every SKILL.md that invokes sub-skills must contain explicit refusal handling language |
| `test_target_skill_invocability.py` | Contract: the target skill of a run_skill call must be invocable after session setup |
| `test_token_summary_contracts.py` | Structural contracts for the token summary pipeline |
| `test_tools_recipe_contracts.py` | Contract tests for tools_recipe.py MCP tool docstrings |
| `test_triage_contracts.py` | Contract tests for triage-issues --enrich flag and requirement enrichment behavior |
| `test_triage_issues_contracts.py` | Contract tests for triage-issues body-file safety (gh issue edit --body-file) |
| `test_version_consistency.py` | Cross-file version consistency: pyproject.toml, __init__.__version__, plugin.json, bundled recipe versions |

## Architecture Notes

`conftest.py` provides `REFUSAL_SIGNALS` constants shared across many contract tests. `_anti_confirm_helpers.py` mirrors the production anti-confirmation regex for structural contract verification.
