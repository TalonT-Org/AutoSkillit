from __future__ import annotations

import collections
from pathlib import Path

import pytest

from tests.arch._helpers import SRC_ROOT
from tests.arch._subpackage_isolation_file_counts_authoring import AUTHORING_FILE_COUNT_LIMITS
from tests.arch._subpackage_isolation_file_counts_foundation import FOUNDATION_FILE_COUNT_LIMITS
from tests.arch._subpackage_isolation_file_counts_runtime import RUNTIME_FILE_COUNT_LIMITS
from tests.arch._subpackage_isolation_file_counts_tooling import TOOLING_FILE_COUNT_LIMITS

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

FILE_COUNT_LIMITS = collections.ChainMap(
    FOUNDATION_FILE_COUNT_LIMITS,
    AUTHORING_FILE_COUNT_LIMITS,
    RUNTIME_FILE_COUNT_LIMITS,
    TOOLING_FILE_COUNT_LIMITS,
)


def test_server_file_count_under_limit() -> None:
    """server/ must not exceed 28 Python files (REQ-DSGN-002).

    Limit updated from 14 to 16 after tools_integrations was split into
    tools_github, tools_issue_lifecycle, and tools_pr_ops.
    Limit updated from 16 to 17 after _editable_guard.py was added as
    the pre-deletion editable install guard for perform_merge().
    Limit updated from 17 to 18 after _lifespan/_lifespan.py was added for
    FastMCP server lifespan teardown (#745).
    Limit updated from 18 to 19 after _wire_compat.py was added for
    Claude Code wire-format sanitization middleware.
    Limit updated from 19 to 20 after _session_type.py was added for
    session-type tag visibility dispatch (3-branch startup logic).
    Limit updated from 20 to 22 after tools_ci.py was split into
    tools_ci_watch.py and tools_ci_merge_queue.py submodules.
    Limit updated from 22 to 23 after _guards.py was extracted from helpers.py.
    Limit updated from 23 to 24 after _subprocess.py was extracted from helpers.py.
    Limit updated from 24 to 25 after _misc.py was extracted from helpers.py.
    Limit updated from 25 to 28 after #4557 decomposed _recipe_delivery.py
    into _recipe_artifact.py + _recipe_delivery_helpers.py + _recipe_delivery.py,
    and _recipe_section_pagination.py into _recipe_section_planning.py +
    _recipe_section_pagination.py.
    """
    py_files = list((SRC_ROOT / "server").glob("*.py"))
    assert len(py_files) <= 28, f"server/ has {len(py_files)} files, max is 28"


def test_no_subpackage_exceeds_10_files() -> None:
    """REQ-CNST-003: No sub-package directory may contain more than 10 Python files.

        Exemptions (rule ID | rationale):
          server/ — REQ-CNST-003-E1: server/ splits tool handlers into per-domain files
            (tools_clone, tools_github, tools_issue_headless, tools_issue_labels, tools_pr_ops,
            tools_ci, tools_git, tools_recipe, tools_status, tools_workspace, tools_execution,
            tools_kitchen, helpers, git, _factory, _state, __init__); each file is a thin
            routing layer. Exempt at 16 files.
            _progress_heartbeat.py adds the MCP progress-notification context manager,
            bringing the count to 28.
          recipe/ — REQ-CNST-003-E2: recipe/ hosts one file per semantic-rule domain
            (rules_bypass, rules_ci, rules_clone, rules_packs, etc.) for independent testability.
            Adding rules_cmd.py for run_cmd echo-capture alignment validation and
            rules_isolation.py for workspace isolation checks brings the count to 30.
            rules_blocks.py adds the block-level budget rule family, bringing the count to 32.
            rules_reachability.py adds symbolic BFS reachability rules, bringing the count to 33.
            rules_fixing.py adds conditional-write-skill ungated-push detection,
            bringing the count to 34.
            rules_campaign_dispatch.py, rules_campaign_deps.py, rules_campaign_ingredients.py,
            rules_campaign_capture.py, and rules_campaign_flow.py split rules_campaign.py,
            bringing the count to 37.
            rules_temp_path.py adds the non-unique-output-path lint rule for output path
            isolation enforcement, bringing the count to 39.
            identity.py adds recipe identity hashing (content and composite fingerprints),
            bringing the count to 40.
            order.py adds the stable display order registry (BUNDLED_RECIPE_ORDER) for
            Group 0 bundled recipes, bringing the count to 41.
            Monolithic file splits (_api.py → _recipe_ingredients + _recipe_composition;
            _analysis.py → _analysis_graph + _analysis_bfs + _analysis_blocks +
            _analysis_detectors) add 6 files, bringing the count to 47.
            _skill_helpers.py extracts the shared _get_skill_category_map helper from
            rules_skills.py and rules_features.py to eliminate duplication,
            bringing the count to 48.
            rules/rules_callable_scope.py adds the callable-requires-scoped-discovery
            rule enforcing scoped directory arguments for file-discovering callables,
            bringing the rules/ count to 29. rules/rules_remediation.py adds the
            audit-impl-remediation-route rule ensuring remediation_path captures have
            non-terminal non-GO routes, bringing the rules/ count to 30.
            rules/rules_loop_progress.py adds the loop-body-uncaptured-output rule
            ensuring run_skill steps inside routing cycles capture declared outputs,
            bringing the rules/ count to 31.
            rules_phoropter_adjacency.py adds phoropter phase-order and step-interleaving
            semantic validation rules, bringing the count to 50.
            rules_loop_counter.py adds loop-counter-cross-path-sharing and
            loop-guard-before-verify semantic rules, bringing the count to 51.
            Decomposition into campaign/, ci/, dataflow/, graph/ subdirectories moved
            files out of rules/, bringing the rules/ count to 35.
            rules_stamp_ownership.py adds the exclusive-stamp-ownership enforcement
            rule, bringing the rules/ count to 36.
            rules_gitignored_deliverable.py adds the gitignored-deliverable-in-plan
            rule flagging plan steps writing to gitignored paths that feed audit-impl,
            bringing the rules/ count to 37.
            rules_contract_recovery.py adds the contract-recovery-requires-salvage-route
            ERROR rule deriving on_context_limit salvage-route requirements from skill
            contract capability (#4305 part C), bringing the rules/ count to 38.
            Exempt at 51 files.
          execution/ — REQ-CNST-003-E3: execution/ decomposes process lifecycle into
            focused single-concern modules (_process_io, _process_kill, _process_race,
            etc.) that cannot be merged without re-introducing the coupling they isolate.
            recording.py adds the RecordingSubprocessRunner decorator as a separate module
            to keep scenario recording concerns isolated from the core process lifecycle.
            _headless_recovery.py owns both result recovery and write-path JSONL scanning.
            _headless_recovery.py, _headless_path_tokens.py, and _headless_result.py
            split the remaining headless.py concern groups into private sub-modules
            following the _process_*.py precedent (P8-F1), bringing the count to 29.
            _session_model.py and _session_content.py split session.py (P8-F3),
            _merge_queue_classifier.py and _merge_queue_repo_state.py split merge_queue.py
            (P8-F4), bringing the count to 33.
            _retry_fsm.py and _session_outcome.py split session retry and outcome logic,
            bringing the count to 35.
            _merge_queue_group_ci.py extracts merge-group CI helpers and GraphQL mutation/query
            strings from merge_queue.py to satisfy the 500-line size budget (P8-F4 follow-up),
            bringing the count to 36.
            _headless_git.py extracts git LOC-capture helpers (_capture_git_head_sha,
            _parse_numstat, _compute_loc_changed) from headless.py to keep it under the
            750-line architectural budget, bringing the count to 37.
            _recording_skills.py adds snapshot/restore helpers for ephemeral skill dirs in
            the record/replay system, isolated from recording.py to keep snapshot logic
            independently testable, bringing the count to 38.
            Exempt at 38 files.
          core/ — REQ-CNST-003-E4: core/ types split into per-concern type modules
            (_type_enums, _type_protocols_logging, _type_protocols_execution,
            _type_protocols_github, _type_protocols_workspace, _type_protocols_recipe,
            _type_protocols_infra, _type_results, _type_subprocess, etc.) to
            prevent circular imports while keeping IL-0 types co-located. Also houses
            _terminal_table.py as the IL-0 shared terminal rendering primitive so that
            both cli/ (IL-3) and pipeline/ (IL-1) can import it without layer violations.
            _claude_env.py adds the canonical IDE-scrubbing env builder for all
            claude subprocess launches. kitchen_state.py adds the stdlib-only
            kitchen-open session marker reader for hook subprocesses.
            _version_snapshot.py adds the process-scoped version snapshot for session
            telemetry (collect_version_snapshot, lru_cache'd).
            _plugin_cache.py adds the plugin cache lifecycle: retiring cache sweep,
            install locking, and kitchen registry (accessible from server/ without
            cli/ import).
            _plugin_artifact_identity.py isolates exact installed-artifact manifest
            validation from retirement-cache orchestration so both IL-0 authorities
            remain below the source-module line limit.
            feature_flags.py adds the IL-0 is_feature_enabled() primitive — must live
            in core/ to be importable by all layers without cross-layer violations.
            session_registry.py adds the stdlib-only session registry mapping
            autoskillit launch IDs to Claude Code session UUIDs for the scoped
            resume picker.
            tool_sequence_analysis.py adds the stdlib-only cross-session tool call
            sequence DFG analysis (IL-0; must live in core/ to be importable by server/).
            Monolithic protocol module split into 6 domain-grouped shard files (net +5 files).
            _install_detect.py adds the is_dev_install() predicate for config resolution
            to auto-detect whether the install is editable when experimental_enabled is absent,
            bringing the count to 33.
            _type_session_env.py adds FleetSessionEnv frozen dataclass for typed env spec
            at the session launch boundary, bringing the count to 20.
            _type_backend.py adds BackendCapabilities frozen dataclass and CLAUDE_CODE_CAPABILITIES
            constant for backend capability declarations (IL-0), bringing the count to 21.
            _type_token.py adds CanonicalTokenUsage frozen dataclass for provider-agnostic
            token usage normalization (IL-0), bringing the count to 22.
            _type_exceptions.py adds RecipeLoadError hierarchy (ProcessStaleError,
            RecipeNotFoundError) for exception-based error propagation from
            load_and_validate, bringing the count to 23.
            _type_phoropter.py adds frozen phoropter family/phase types
            (PhoropterPrescription, ReadingToken, PhoropterPhaseSkip,
            CrossDomainPrescription, CrossDomainAssessment) for the phoropter
            registry system, bringing the core/types count to 29.
            _type_tradition_manifest.py adds TraditionManifest, LensEntry,
            DialingConfig frozen dataclasses with from_yaml_path YAML loader
            for the tradition manifest system, bringing the core/types count to 30.
            _type_invariant_registry.py adds InvariantDef frozen dataclass and
            INVARIANT_REGISTRY mapping 13 prose prohibitions to runtime gate targets,
            bringing the core/types count to 31.
            _type_recipe_sections.py adds recipe-section schema and digest contracts.
            _type_skill_contract.py adds the backend-neutral SkillSourceRef identity
            consumed by workspace projections.
            _context_admission.py adds the pure context-admission reducer, and
            _type_context_admission.py adds its frozen IL-0 contract records.
            Exempt at 26 files (core/types: 36).
          cli/ — REQ-CNST-003-E5: cli/ retains _terminal_table.py as a re-export shim
            for backward-compatible cli/ imports; canonical implementation lives in
            core/_terminal_table.py. Also contains _terminal.py — the terminal state
            management context manager (terminal_guard) for interactive subprocess
            sessions. _update_checks.py adds the unified update check orchestration.
            _update.py adds the first-class update subcommand implementation.
            _fleet.py adds fleet error envelope rendering for CLI consumers.
            _features.py adds feature gate inspection subcommand (list/status).
            _session_picker.py adds the scoped session resume picker that filters
            sessions by type (cook/order) using the session registry.
            _doctor.py was split (1245 lines → facade + 9 sub-modules) following the
            _process_*.py pattern: _doctor_types.py (shared DoctorResult type),
            _doctor_mcp.py, _doctor_hooks.py, _doctor_install.py, _doctor_config.py,
            _doctor_runtime.py, _doctor_env.py, _doctor_features.py, _doctor_fleet.py.
            The CLI is organized as: `cli/prompts/` (prompt builders — _prompts,
            _prompts_campaign, _prompts_kitchen, _prompts_orchestrator),
            `cli/install/` (install cluster — _install_contract, _install_info,
            _installed_plugins, _marketplace, _plugin_artifact), `cli/ops/`
            (diagnostic subcommand runners — _capture_store, _codex_attempts,
            _codex_orphans, _daemon_orphans, _process_orphans, _sessions),
            `cli/session/` (cook/order lifecycle — _session_cook, _session_order,
            _session_onboarding, _session_launch, _session_backend,
            _session_constants, _session_picker, _session_process,
            _session_reload, _session_startup_trace), `cli/update/` (update
            pipeline — _update, _update_checks, _update_checks_source,
            _update_checks_fetch, _transaction, _obligation_repair, _restart),
            and `cli/doctor/` (doctor commands — _doctor_types, _doctor_mcp,
            _doctor_hooks, _doctor_install, _doctor_config, _doctor_runtime,
            _doctor_env, _doctor_features, _doctor_fleet, _doctor_skills,
            _doctor_capture_store, plus the facade).
            The 11 remaining top-level files (app.py + 10 small shared utilities —
            see the dict entry below) are the orchestration entry points and shared
            helpers that have no coherent subpackage home.
            Codex config.toml hook generation and sync
    (generate_codex_hooks_config, sync_hooks_to_codex_config) live in
    execution/backends/_codex_hooks.py paralleling _hooks.py for Claude Code
    settings.json hooks.
    Exempt at 11 files.
          hooks/ — REQ-CNST-003-E6: hooks/ hosts one standalone script per hook event
            (PreToolUse, PostToolUse, SessionStart). Each script must remain a separate
            file so Claude Code can invoke it directly as a subprocess. pretty_output_hook.py
            additionally owns a set of underscore-prefixed private formatter modules
            (_fmt_primitives.py, _fmt_execution.py, _fmt_status.py, _fmt_recipe.py)
            that are imported helpers — not standalone hook scripts — split out to
            keep pretty_output_hook.py under its line budget. ask_user_question_guard.py
            gates AskUserQuestion on kitchen-open state. grep_pattern_lint_guard.py adds
            input-validation guard for Grep tool BRE pattern syntax. review_gate_post_hook.py
            and review_loop_gate.py add the review gate enforcement hooks. recipe_write_advisor.py
            adds a non-blocking advisory hook for recipe YAML writes. write_guard.py
            blocks Write/Edit outside the allowed prefix in read-only skill sessions.
            _hook_utils.py provides shared stdlib-only utilities (e.g., find_project_root)
            for hook scripts that need common path resolution logic.
            _command_classification.py adds shared command classification primitives
            (interpreter/wrapper detection) for all command-classifying guards.
            quota_guard_state_post_hook.py is a stdlib-only PostToolUse script that
            maintains the per-session quota-disable marker. Exempt at 15 files.
            output_budget_guard.py was deleted and retired; its enforcement moved to
            shell_capture_hook.py (input-rewrite mechanism) at the hooks/ package root.
            Exempt at 32 files.
          pipeline/ — REQ-CNST-003-E7: pipeline/ added github_api_log.py for session-scoped
            GitHub API request tracking (DefaultGitHubApiLog accumulator + GitHubApiEntry).
            context_admission_ledger.py adds crash-safe shadow accounting, and
            recipe_initialization.py adds the pure named-recipe lifecycle reducer.
            Exempt at 14 files.
          fleet/ — REQ-CNST-003-E8: fleet/ added _semaphore.py for FleetSemaphore, the
            configurable asyncio.BoundedSemaphore implementation of the FleetLock protocol.
            Placed in fleet/ rather than server/ to preserve conservative test-filter cascade
            narrowing: changes to fleet/_semaphore.py only cascade to fleet/ tests, not to
            server/ tests. state.py was decomposed into state_types.py, state_gates.py, and
            state_recovery.py to reduce the 757-line monolith and centralize deserialization
            logic on DispatchRecord.from_dict. Startup warming lives here so its
            execution/fleet imports remain layer-correct. state_types.py was then further
            decomposed into state_effects.py, state_records.py, state_transitions.py,
            state_outcomes.py, and state_error_codes.py (#4856) to split the 899-line monolith
            along effect-provenance, dispatch-record/campaign-state, transition/retry, and
            outcome/result boundaries, after which the transitional state_types.py re-export
            facade was deleted. Exempt at 28 files.
    """
    violations: list[str] = []
    dirs_to_check: list[Path] = []
    for sub_dir in sorted(SRC_ROOT.iterdir()):
        if not sub_dir.is_dir() or sub_dir.name.startswith("_") or sub_dir.name == "__pycache__":
            continue
        dirs_to_check.append(sub_dir)
        for nested_dir in sorted(sub_dir.iterdir()):
            if (
                not nested_dir.is_dir()
                or nested_dir.name.startswith("_")
                or nested_dir.name == "__pycache__"
            ):
                continue
            dirs_to_check.append(nested_dir)
    for sub_dir in dirs_to_check:
        rel_key = str(sub_dir.relative_to(SRC_ROOT))
        py_files = list(sub_dir.glob("*.py"))
        limit = FILE_COUNT_LIMITS.get(rel_key, 10)
        if len(py_files) > limit:
            violations.append(f"{rel_key}/: {len(py_files)} Python files (max {limit})")
    assert not violations, "Sub-packages exceeding 10 Python files:\n" + "\n".join(
        f"  {v}" for v in violations
    )
