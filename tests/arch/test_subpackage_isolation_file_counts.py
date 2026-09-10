from __future__ import annotations

from pathlib import Path

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

# Per-issue #4671: shim files at core/ and recipe/ top level are 2-line forwarding
# re-exports that preserve old import paths after moving real implementations into
# sub-packages. The arch test excludes these from the file count because they
# contribute no real module surface; only the underlying real modules are counted.
# Populated by Phase A (core/) and Phase D (recipe/) of issue #4671.
_SHIM_FILENAMES: frozenset[str] = frozenset()
_RECIPE_SHIM_FILENAMES: frozenset[str] = frozenset()

FILE_COUNT_LIMITS: dict[str, int] = {
    "core": 49,  # +_managed_worker_capacity shared fleet/fixed-batch authority
    # _type_truth replaces the retired _type_tradition_manifest shard.
    "core/types": 76,
    "core/runtime": 11,
    "config": 20,
    "recipe": 53,  # +7 shards added by issue #4905 decomposition of _api_orchestration.py
    "recipe/rules": 66,
    "server": 20,
    "execution": 23,
    "cli": 9,
    "cli/session": 11,
    "cli/doctor": 13,
    "pipeline": 19,
    "fleet": 20,
    "server/tools": 39,
    "execution/process": 11,
    "execution/backends": 30,
    "execution/github_review": 15,
    "execution/headless": 15,
    "execution/session": 20,
    "workspace": 32,
    "hooks": 27,  # +1 _capture_spawn.py extracted from _capture_process.py (#4732)
    "hooks/guards": 41,
    "smoke_utils": 11,
}


def test_server_file_count_under_limit() -> None:
    """server/ must not exceed 20 Python files (REQ-DSGN-002).

    Twenty is a root package a single reviewer can still hold in mind.
    Responsibilities that would push the count past it belong in a
    grouping subpackage instead — see `server/recipe/`, `server/lifecycle/`,
    and `server/response/` (issue #4673). None of the three carries a
    dedicated `FILE_COUNT_LIMITS` entry; they are governed by the default
    10-file ceiling in `test_no_subpackage_exceeds_10_files` below, plus the
    parameterized per-package cases in `tests/arch/test_server_fleet_folder_layout.py`.
    """
    limit = FILE_COUNT_LIMITS["server"]
    py_files = list((SRC_ROOT / "server").glob("*.py"))
    assert len(py_files) <= limit, f"server/ has {len(py_files)} files, max is {limit}"


def test_no_subpackage_exceeds_10_files() -> None:
    """REQ-CNST-003: No sub-package directory may contain more than 10 Python files.

        Exemptions (rule ID | rationale):
          server/ — REQ-CNST-003-E1: server/ retains composition, git, exploration, audit,
            notification, and utility modules at the root -- grouping them would buy no
            coherence. Recipe, lifecycle, and response concerns instead live in their own
            subpackages (issue #4673: server/recipe/, server/lifecycle/, server/response/).
            Exempt at 20 files -- a root package a single reviewer can still hold in mind.
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
            _type_phoropter.py adds frozen PhoropterPrescription and ReadingToken
            types for the phoropter registry system, bringing the core/types count to 29.
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
            server/ tests. The nine campaign-state modules (state, state_effects,
            state_error_codes, state_gates, state_outcomes, state_records, state_recovery,
            state_transitions, _state_lock) moved into their own fleet/campaign_state/
            subpackage (issue #4673), each retaining its basename; fleet's remaining
            sidecar, dispatch, parsing, and prompt concerns do not form a shared
            responsibility that would justify grouping them into a further package.
            Exempt at 20 files -- a root package a single reviewer can still hold in mind.
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
        # Pick the correct shim registry based on package (issue #4671).
        if rel_key.startswith("recipe/"):
            shim_set = _RECIPE_SHIM_FILENAMES
        elif rel_key.startswith("core/") or rel_key == "core":
            shim_set = _SHIM_FILENAMES
        else:
            shim_set = frozenset()
        py_files = [p for p in sub_dir.glob("*.py") if p.name not in shim_set]
        limit = FILE_COUNT_LIMITS.get(rel_key, 10)
        if len(py_files) > limit:
            violations.append(f"{rel_key}/: {len(py_files)} Python files (max {limit})")
    assert not violations, "Sub-packages exceeding 10 Python files:\n" + "\n".join(
        f"  {v}" for v in violations
    )
