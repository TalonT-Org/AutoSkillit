from __future__ import annotations

_LINE_LIMIT_EXEMPTIONS: dict[str, tuple[int, str]] = {
    "hooks/_capture/_runner.py": (
        1050,
        "REQ-CNST-010-E31: #4511 requires a dedicated setup failure boundary before "
        "the command-body handler because the latter assumes an initialized artifact; "
        "keeping both stages in the runner preserves one owner for capture settlement",
    ),
    "core/types/_type_constants.py": (
        1050,
        "REQ-CNST-010-E29: #4597 Phase 3 added a RETIRED_INSTALL_ARTIFACT_SHAPES entry "
        "for the pre-immutable-roots shared uv tool root and two DURABLE_ARTIFACT_WRITERS "
        "entries (the entrypoint shim, the install-root generation writer). Both registries "
        "are append-only forcing functions per the file's own model comment; splitting them "
        "out of this module would separate them from the frozensets and validation "
        "functions their own docstrings and tests reference by exact module path.",
    ),
    "execution/evidence_reader.py": (
        1500,
        "REQ-CNST-010-E25: #4585 keeps sterile auth, projection, probes, managed process "
        "lifecycle, and strict result validation behind one evidence-reader launch interface",
    ),
    "execution/process/__init__.py": (
        1050,
        "REQ-CNST-010-E27: #4678 rectify threads ceiling_seconds through run_managed_async/"
        "run_managed_sync/DefaultSubprocessRunner and adds the PTY-wrapper workload-identity "
        "resolution for the process-tether spawner-death immunity mechanism — this facade is "
        "the single composition point for both spawn paths and must stay adjacent to the "
        "spawn call sites it wires the tether into.",
    ),
    # REQ-CNST-010-E1: core/types.py is the canonical type registry for the entire
    # package. It defines all StrEnums, protocols, constants, and shared type aliases
    # in one place to prevent circular imports across sub-packages. Exempt at 1200 lines.
    "types.py": (
        1200,
        "REQ-CNST-010-E1: canonical type registry — wide surface required to prevent "
        "circular imports; all enums/protocols/constants consolidated here",
    ),
    "hooks/_capture_artifacts.py": (
        1200,
        "REQ-CNST-010-E22: descriptor-anchored capture authority and isolated runner — "
        "re-exports capture_store_stats, reconcile_capture_store, CaptureStoreStats, "
        "CleanupBlocker, CleanupProgress, and SweepBudgetSpec from its own dual-mode "
        "(flat sys.path / dotted package) _capture import bootstrap so hooks/__init__.py "
        "can gateway them to cli/ops/_capture_store.py without importing _capture submodules "
        "directly, which would race the standalone hook scripts' own flat-style bootstrap "
        "of sys.modules['_capture']. Bumped for ADR-0009's failure-disposition routing "
        "(bookkeeping vs. integrity) and the capacity injection seam (issue #4479).",
    ),
    "hooks/_capture_lifecycle/_store.py": (
        1250,
        "REQ-CNST-010-E28: post-split capture-lifecycle store (#4727) — the "
        "4 admission helpers (_acquire_flock, _admission_reason, _admit_new_record, "
        "_scan_and_adopt_orphans) are now thin wrappers around module-level "
        "implementations in the sibling _admission.py, but the rest of the class "
        "body (state-machine transitions, ledger-compaction, capacity-rescue, "
        "delivery wiring, sweep orchestration) shares the same self-accounting "
        "invariants the original E21 entry called out. The class body alone is "
        "~960 lines after the wrappers extract; the limit stays at 1250 to match "
        "the pre-split E21 ceiling. E21 was retired by issue #4853 (decomposing "
        "hook_registry.py); this entry remains the load-bearing exemption for "
        "_capture_lifecycle/_store.py until the class body is further decomposed "
        "(issue #4727).",
    ),
    "hooks/_capture_contract.py": (
        1100,
        "REQ-CNST-010-E23: CaptureFailureV3 envelope framing — carries the full "
        "CaptureFailureReason wire vocabulary and its (V2 marker) rendering; ADR-0009 "
        "added the SNAPSHOT_INTEGRITY reason and degraded-delivery envelope fields, "
        "which must stay co-located with the rest of the envelope schema they extend "
        "(issue #4479).",
    ),
    "hooks/_command_classification.py": (
        1600,
        "REQ-CNST-010-E10: shared command-classification primitive consumed by all "
        "command-inspecting guards — tokenization, shell-payload extraction, "
        "interpreter-write detection, protected-path reads, and recursive payload "
        "segmentation; the stdlib-only hook boundary and shared parser prevent "
        "policy drift across guard processes. Cap reduced to 1300 by #4665's "
        "decomposition of GitHub mutation cardinality/route authority into the "
        "_github_mutation_analysis.py sibling under E26. Bumped to 1600 for Issue "
        "#4655's rectify: ArgvToken threads quote provenance through the tokenizer "
        "(_tokenize_command_segments_with_redirects, _partition_output_redirect_"
        "indices/_select_executable_argv_tokens, _verb_start_index), and the CLI-"
        "agnostic _FlagArity/_consume_argv_flag/_consume_str_flag spec-table engine "
        "(plus _GIT_GLOBAL_FLAG_SPEC and _PIP_GLOBAL_FLAG_SPEC, and "
        "extract_git_subcommand_and_flags's fail-closed unrecognized-global-flag fix) "
        "-- these are shared, CLI-agnostic primitives every command-inspecting guard "
        "consumes (git, curl, pip, and gh's own spec table in "
        "_github_mutation_analysis.py, which imports this engine rather than "
        "duplicating it), so they stay adjacent to the tokenizer they extend rather "
        "than the gh-specific consumer module the split already separated them from.",
    ),
    "hooks/_github_mutation_analysis.py": (
        1600,
        "REQ-CNST-010-E26: #4665 decomposes the GitHub mutation cardinality/route "
        "analysis out of _command_classification.py into this sibling module — the "
        "gh/curl possible-exec token check, gh issue edit's target/flag grammar, "
        "statically proven fan-out count, gh mutation subcommand classification, and "
        "the recursive cardinality aggregator all share the same mutation authority "
        "and must stay adjacent to one another for test inspection (test_command_"
        "classification.py::TestAnalyzeGitHubMutations). Cap set to 1300 to bound "
        "the shared mutation authority after decomposition. Bumped to 1600 for Issue "
        "#4655's rectify: _GH_API_FLAG_SPEC and _CURL_FLAG_SPEC (this module's own "
        "gh-api/curl flag tables, consuming _command_classification's shared "
        "_FlagArity/_consume_argv_flag engine via a module-scope import) replace "
        "_analyze_gh_api/_analyze_curl_segment's ad-hoc if/elif flag chains so an "
        "unrecognized flag fails closed with a distinguishable reason code instead of "
        "being silently misparsed as a second route; and ArgvToken-typed "
        "_flag_value/_analyze_gh_api/_analyze_curl_segment/_is_dynamic_shell_value/"
        "_is_static_issue_edit_target/_issue_edit_request_count prove GraphQL "
        "documents and flag values shell-inert from quote provenance rather than "
        "content alone -- must stay adjacent to the mutation authority they feed.",
    ),
    "hooks/guards/git_ops_guard.py": (
        1050,
        "REQ-CNST-010-E28: Issue #4655's rectify moves this guard's checked-out-ref "
        "dynamic-value check onto the shared _DYNAMIC_SHELL_TOKEN_RE regex, which "
        "#4665's decomposition relocated to hooks/_github_mutation_analysis.py -- a "
        "second module-scope import block (alongside the existing "
        "_command_classification one) is needed since the two symbols now live in "
        "different sibling modules. Cap bumped from the 1000-line default to give "
        "this guard's own destructive-op/fetch/checked-out-ref classification room "
        "without re-tripping the limit on the next small addition.",
    ),
    "session.py": (
        1060,
        "REQ-CNST-010-E3: session adjudication pipeline — exhaustive match arms "
        "for TerminationReason require explicit IDLE_STALL arms in _compute_success, "
        "_compute_retry, and ClaudeSessionResult.normalize_subtype; "
        "lifespan_started heuristic added",
    ),
    "_doctor.py": (
        1300,
        "REQ-CNST-010-E4: doctor check registry — 28 sequential checks require inline logic; "
        "splitting into sub-modules would obscure the check sequence and break the test "
        "filter cascade",
    ),
    "server/_recipe_delivery.py": (
        750,
        "REQ-CNST-010-E12: #4557 decomposes recipe delivery into _recipe_delivery.py "
        "(finalization orchestrator) and _recipe_artifact.py (persistence, attestation, "
        "helper types).",
    ),
    "server/_recipe_section_pagination.py": (
        750,
        "REQ-CNST-010-E23: #4414 binds terminal completion receipts to the finalized page "
        "content digest inside the existing immutable page renderer so pagination and receipt "
        "identity cannot drift across separate serialization authorities. "
        "#4557 decomposes pagination into sibling modules (_recipe_section_planning, "
        "_recipe_section_rendering) with char-ceiling plumbing and dual-domain page fitting.",
    ),
    "tools_recipe.py": (
        750,
        "REQ-CNST-010-E25: #4557 decomposes get_recipe_section handler into "
        "tools_recipe.py (tool entry points) and tools/_recipe_section_handler.py "
        "(bounded-delivery pull handler and counter injection).",
    ),
    "execution/backends/codex.py": (
        1300,
        "REQ-CNST-010-E9-narrowed: CodexBackend class alone is 1062 lines "
        "(cmd/cmd-spec grammar with build_skill_session_cmd/"
        "build_food_truck_cmd/build_interactive_cmd/"
        "validate_interactive_invocation/setup_session_dir), "
        "with the four cmd-builder methods tightly coupled to CodexBackend "
        "state. CodexBackend retains all five cmd-builder methods because each "
        "touches instance state (capabilities, env policy, flag vocabulary, "
        "session locator) and the cmd-spec grammar is the backend's authority "
        "boundary — splitting these would force a separate mutable state object "
        "and break the protocol. The remaining slimmed file is 1242 "
        "lines; cap lowered from 2500 to 1300 to acknowledge the architectural seam that "
        "the decomposition could not cross without breaking the backend "
        "dataclass invariant.",
    ),
    "execution/backends/claude.py": (
        1600,
        "REQ-CNST-010-E19: Claude backend protocol parity keeps managed native-shell "
        "decision/reference disposition beside executable launch-binding validation; "
        "both are shared builder-interface obligations even though Claude deliberately "
        "does not inject the Codex-only controls; REQ-SEM-ADAPT-001 semantic-plan "
        "adaptation remains on this registered backend so native child syntax and model "
        "alias resolution cannot drift into a second adapter registry; #4443 also threads "
        "parent sandbox authority through the shared no-op setup boundary and explorer "
        "dispatch rendering preserves the same backend-owned syntax authority; #4480 adds "
        "the plugin_dir launch-binding validation parameter for cross-backend signature "
        "parity; #4507 renders one named child per runtime topic (+6 net lines); "
        "#4233 keeps Claude task lifecycle normalization and immutable skill-session "
        "async hardening beside the backend parser and command builder that own them. "
        "#4557 adds Claude-only host-attestation env, version-derived annotation support, "
        "and frozen attestation env at all 4 launch sites; #4566 "
        "adds execution-role protocol parity while preserving Claude behavior (+3 net lines). "
        "Threads mcp_tool_timeout_sec through build_interactive_cmd, "
        "build_skill_session_cmd, build_food_truck_cmd, and build_resume_cmd to give Claude "
        "Code's client-side idle-abort timeout parity with the server-side anyio.fail_after "
        "ceiling (+2 net lines). REQ-017 (resolve-failures iteration 1) also adds an "
        "explicit mcp_tool_timeout_sec parameter to build_headless_cmd and injects the "
        "CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT env var when given, plus hardens all four "
        "existing boundary checks with isinstance(mcp_tool_timeout_sec, (int, float)) "
        "so MagicMock-bearing test mocks no longer raise at the builder (+19 net lines).",
    ),
    "execution/headless/_headless_result.py": (
        900,
        "REQ-CNST-010-E25-narrowed: #4233 keeps the async-obligation success gate adjacent to "
        "the existing stale, idle, timeout, and content adjudication order it must preempt. "
        "After #4664 decomposition, adjudication helpers live in _headless_adjudication.py "
        "— including the #4641/#4644 _should_flag_cleanup_incomplete diagnostic shared by "
        "both SkillResult construction seams; _build_skill_result remains here as the "
        "headless orchestration authority. The 827-line residual is dominated by that "
        "single 741-line function, which owns the success-gate adjacency rule.",
    ),
    "execution/backends/_codex_session_storage.py": (
        1500,
        "REQ-CNST-010-E13-narrowed: CodexSessionStore + CodexInteractiveSessionLease + "
        "_FileLease transaction-boundary core only; stateless FS primitives extracted to "
        "_codex_fs_atomic.py (RE: #4664). The transaction-boundary core remains one "
        "lock-coupled module — splitting _FileLease / CodexInteractiveSessionLease / "
        "CodexSessionStore across multiple files would duplicate the inode-preserving "
        "staging, process/thread/view leases, promotion, index publication, manifest "
        "validation, crash recovery, and explicit legacy-view reconciliation invariants. "
        "Cap lowered to 1500 lines to accommodate the core without the stateless helpers. "
        "#4678 rectify adds spawn-identity capture to _record_spawn and verify-before-mark "
        "identity checks to recover() — both belong to the same transaction boundary as "
        "the leases they gate, and fit under this cap post-extraction.",
    ),
}
