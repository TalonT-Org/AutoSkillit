# P1-A7-WP2 Disposition Table — String-to-Capability, Conformance, Builder-Divergence, Guard-Bypass, D9

Generated: 2026-06-27
Branch: t5-p1-a7-wp2-produce-a-structured-disposition-table-for-the/4005
Depends on: P1-A7-WP1 (Strategic & SessionLocator cluster — 20 issues dispositioned)

## Consolidated Disposition Table

Columns: `issue_number | title_short | disposition | evidence | generator | notes`

### §2 — String-to-Capability Series (P1–P6: #3103–#3137)

| Issue | Title Short | Disposition | Evidence | Generator | Notes |
|-------|-------------|-------------|----------|-----------|-------|
| #3103 | Extend BackendCapabilities with 18 fields | SUPERSEDED | `abb2bb0e3` (PR #3143) | R-G/P1 | |
| #3104 | CodexBackend 18 capability kwargs | SUPERSEDED | `e238d4402` (PR #3151) | R-G/P1 | |
| #3105 | Protocol method stubs | SUPERSEDED | `bc2215a22` (PR #3169) | R-G/P1 | |
| #3106 | ClaudeCodeBackend version/list_plugins/validate_skill_content | SUPERSEDED | `b37b41c81` (PR #3186) | R-G/P1 | |
| #3107 | CodexBackend version() method | SUPERSEDED | Codebase state: `CodexBackend.version()` exists at `src/autoskillit/execution/backends/codex.py:1077` | R-G/P1 | No commit references #3107 directly; method present in codebase |
| #3108 | Backend Protocol completeness tests | SUPERSEDED | `6db448bde` (PR #3224) | R-G/P1 | |
| #3109 | _adapt_agent_result unified dispatch | SUPERSEDED | `d39c84b3b` (PR #3223) | R-G/P1 | |
| #3110 | triage_staleness signature → CodingAgentBackend | SUPERSEDED | GitHub CLOSED | R-G/P1 | No commit references #3110; issue closed |
| #3111 | _version_snapshot.py Protocol dispatch | SUPERSEDED | `52d4ba45b` (PR #3560) | R-G/P1 | |
| #3112 | session_skills.py string elimination | SUPERSEDED | `ecbc963aa` (PR #3524) | R-G/P1 | |
| #3113 | validate_project_local_skill_dir backend-agnostic | SUPERSEDED | `8dc9b6b3e` (PR #3523) | R-G/P1 | |
| #3114 | channel_b_capable Codex log dispatch tests | SUPERSEDED | `7aca7aaa8` (PR #3525) | R-G/P1 | |
| #3115 | recording.py capability replacement | SUPERSEDED | `63d25bea5` (PR #3532) | R-G/P1 | |
| #3116 | Hoist backend + string comparison replacement | SUPERSEDED | `f15b799c3` (PR #3544) | R-G/P1 | |
| #3117 | _apply_triage_gate capability lookup | SUPERSEDED | Codebase state: `triage_capable` field on `BackendCapabilities` at `src/autoskillit/core/types/_type_backend.py:86`; consumed in `src/autoskillit/_llm_triage.py:107` and `src/autoskillit/server/_misc.py:211` | R-G/P2 | No commit references #3117 directly; capability lookup confirmed live |
| #3118 | Backend dispatch guard → capability | SUPERSEDED | `ebcb7c105` (PR #3553) | R-G/P2 | |
| #3119 | Capability-driven plugin guard | SUPERSEDED | `453dbc881` (PR #3578) | R-G/P2 | |
| #3120 | Refactor install() capability-driven | SUPERSEDED | `7e6542af3` (PR #3588) | R-G/P2 | GitHub CLOSED |
| #3121 | Doctor MCP checks → typed params | SUPERSEDED | `6f73a6a72` (PR #3577) | R-G/P2 | |
| #3122 | _check_codex_version → CodingAgentBackend param | SUPERSEDED | `33528a422` (PR #3595) | R-G/P2 | |
| #3123 | AUTOSKILLIT_APPLICABLE_GUARDS constant | SUPERSEDED | `8407f2b1c` (PR #3587) | R-G/P2 | |
| #3124 | test_backend_compliance.py | SUPERSEDED | `95b745031` (PR #3594); superseded by `4cadec7f8` (PR #3991) which delivered the full `TestCodingAgentBackendConformance` class | R-G/P5 | **Double-listed in §3 — counted once in summary** |
| #3125 | Confirm P2-A8 arch tests present | SUPERSEDED | Gate satisfied: P2-A8 tests delivered by #3108 (`6db448bde`) | R-G/P5 | Verification gate, not implementation |
| #3126 | Backend coherence arch tests | SUPERSEDED | `3af6cf6e9` (PR #3599) | R-G/P5 | |
| #3127 | BackendCapabilities no-arg constructible | SUPERSEDED | `99b3a9eb9` (PR #3596) | R-G/P5 | |
| #3128 | DeprecationWarning from commands.py shims | SUPERSEDED | `a945f7c94` (PR #3592) | R-G/P5 | |
| #3129 | 'Adding a new backend' checklist | SUPERSEDED | `0afc558f0` (PR #3608) | R-G/P5 | |
| #3130 | KNOWN_BACKEND_NAMES validation | SUPERSEDED | `dfdf54504` (PR #3598) | R-G/P5 | |
| #3131 | BackendConventions + skills_subdir field | SUPERSEDED | `9975e54cf` / `739cbf9da` (PR #3626, #3620) | R-G/P6 | |
| #3132 | conventions on ClaudeCodeBackend | SUPERSEDED | `4bbe10f6a` (PR #3613) | R-G/P6 | |
| #3133 | conventions on CodexBackend | SUPERSEDED | `b2357dd97` (PR #3614) | R-G/P6 | |
| #3134 | conventions-based skills_subdir lookup | SUPERSEDED | `8e2e552f5` (PR #3623) | R-G/P6 | |
| #3135 | Delete _SKILLS_SUBDIR aliases | SUPERSEDED | `ae309ac2a` (PR #3622) | R-G/P6 | |
| #3136 | SessionIndexEntry.claude_code_log annotation fix | SUPERSEDED | `144577cde` (PR #3597) | R-G/P6 | |
| #3137 | setup_session_dir stub + compliance tests | SUPERSEDED | `e0c8322f1` (PR #3628) | R-G/P6 | |

**Generator column for all 35 string-to-capability entries:** `R-G/P1-P6`.

### §3 — Conformance-Test Series (#3124, #3273, #3385, #2841)

| Issue | Title Short | Disposition | Evidence | Generator | Notes |
|-------|-------------|-------------|----------|-----------|-------|
| #3124 | test_backend_compliance.py | SUPERSEDED | `95b745031` (PR #3594); superseded by `4cadec7f8` (PR #3991) which delivered the full conformance class | R-G/P5 | Original compliance file evolved into `TestCodingAgentBackendConformance` with 31 methods (extended by PR #4081). **Counted once in §2 above.** |
| #3273 | Backend-parametrized CLI tests | SUPERSEDED | `4537136a8` (PR #3299); conformance class parametrized over `BACKEND_REGISTRY` covers both backends | R-G | Cross-validation contract coverage delivered by parametrized conformance tests |
| #3385 | Codex Backend Architectural Immunity | ABSORBED | WP1 mapped to R-A/A1, R-A/A3; 8 forward-declared capability fields remain without production consumers; `CodexBackend.build_inspector_cmd()` at `codex.py:1117-1119` raises `AssertionError("inspector_capable is True but build_inspector_cmd has no implementation")` | R-G | Partially delivered: capability consumption arch test exists (`test_capability_consumption.py`) but enforcement completeness gaps remain |
| #2841 | Ticket Grouper effort-based splitting | INDEPENDENT | N/A | R-G/PY8 | **Discrepancy:** PY8 referenced this as "Conformance test class parametrized over both backends" but current issue content is about audit skill's ticket grouper (effort-based splitting for multi-file findings). Current title/content is INDEPENDENT — no program WP covers ticket grouper improvements. If intended as conformance: SUPERSEDED by `4cadec7f8` |

### §4 — Builder-Divergence (#3699)

| Issue | Title Short | Disposition | Evidence | Generator | Notes |
|-------|-------------|-------------|----------|-----------|-------|
| #3699 | Codex Backend Systemic Builder Divergence | ABSORBED | Partial delivery: `b2e750f7f` (PR #3734) delivered shared base + closed categorization + semantic flag validation. `BackendCmdBuilderBase` ABC at `src/autoskillit/execution/backends/_backend_cmd_builder_base.py:64`. `CodexBackend` inherits from `BackendCmdBuilderBase` at `codex.py:521`; `ClaudeCodeBackend` inherits at `claude.py:276`. WP1 mapped to R-A/A2 | R-G | Shared base delivered; 7 build methods consolidated onto `CodexBackend` (`build_cmd`, `build_headless_cmd`, `build_skill_session_cmd`, `build_food_truck_cmd`, `build_interactive_cmd`, `build_resume_cmd`, `build_inspector_cmd`) — consolidation progressed further than expected. Remaining R-A/A2 work tracks ongoing builder unification. |

### §5 — Guard-Bypass (#2936, #3386)

| Issue | Title Short | Disposition | Evidence | Generator | Notes |
|-------|-------------|-------------|----------|-----------|-------|
| #2936 | Hook-based runtime enforcement for fleet config limits | INDEPENDENT | No commit evidence; no program WP absorbs this. `max_total_issues` and `max_issues_per_food_truck` have zero runtime enforcement (prompt-only). `max_concurrent_dispatches` IS enforced by FleetSemaphore | User-specified | Depends on #2935 (configure_fleet hook config overlay). Neither P1 WPs nor later phases cover fleet-limit hook enforcement |
| #3386 | Standardize SKILL.md subagent spawn instructions | SUPERSEDED | `7d085f7eb` — PR #3386 merged. Note: #3386 is a PR number, not an issue number (GitHub issue query returns NOT_FOUND) | User-specified | Already delivered and merged; cross-provider SKILL.md standardization complete |

### §6 — D9 Independent Bugs

| Issue | Title Short | Disposition | Evidence | Generator | Notes |
|-------|-------------|-------------|----------|-----------|-------|
| #3297 | Codex config.toml TOML destructive overwrite | INDEPENDENT | No fix commit; issue OPEN with `staged` label | D9 | |
| #3877 | Expired /dev/shm SKILL.md paths | INDEPENDENT | Partial: `279cd0323` (PR #3882) added tests only | D9 | 59% of Codex sessions affected; ephemeral cleanup race |
| #3756 | Codex NDJSON Parser Schema Drift | INDEPENDENT | Partial: `bb07f6c7b` (PR #3765) sealed enum immunity | D9 | Dual-schema reconciliation still needed |
| #3876 | .git read-only sandbox (worktree broken under Codex) | INDEPENDENT | Partial: `dfd0902df` (PR #3880) worktree creation sandbox immunity | D9 | Fundamental sandbox limitation |
| #3638 | MCP tool timeout 120s kills long-running tools | INDEPENDENT | Partial: `305e6f6a5` (PR #3640) | D9 | |
| #3676 | CODEX_MODEL_ALIASES stale model IDs | INDEPENDENT | Partial: `041239fb4` (PR #3681) | D9 | o4-mini instead of gpt-5.5 |
| #3836 | run_skill codex_status misclassification | INDEPENDENT | Partial: `60426b2cd` (PR #3838) reclassified to works-as-is | D9 | Blocks 14 skills from Codex |
| #3781 | test_check codex_status misclassification | INDEPENDENT | Partial: `c9aac2db5` (PR #3804) capability classification immunity | D9 | Blocks 5 skills from Codex |

## PY1–PY8 / T4 Named Subset Coverage

R-G's explicit issue list (G1r) names no individual PY-series issue numbers (#2667–#2714).
All PY/T4 issues that appear in R-G's scope are covered via their respective named clusters:

- **SessionLocator WPs (#3922–#3930):** Dispositioned in WP1 as SUPERSEDED (12 entries).
- **Conformance asks (#3124, #3273):** Dispositioned in §3 above.
- **Immunity umbrella (#3385):** Dispositioned in §3 above.
- **Builder-divergence (#3699):** Dispositioned in §4 above.
- **#2841 (PY8):** Dispositioned in §3 above.

No exhaustive triage of the full PY1–PY8 (#2667–#2714) or T4 (#3916–#3937) populations
is performed per acceptance criteria. Only the R-G and D9 explicitly named subsets are covered.

## Gap Registry — Unassigned Absorbed Issues

Issues classified as ABSORBED whose absorbing program WP has not yet been assigned
or maps to a requirement group without a concrete WP:

| Issue | Absorbing Ref | Status | Action Needed |
|-------|--------------|--------|---------------|
| #55 | R-A/A1 | unassigned | R-A is a requirement group, not a concrete P1 WP. No P1 WP owns A1. Needs P2-A9 WP creation or later-phase mapping. |
| #822 | R-A/A1 | unassigned | Same as #55 — R-A/A1 has no P1 WP. |
| #824 | R-F/F1 | unassigned | R-F is later phase. F1 paper-backend exercise has no assigned WP. |
| #820 | R-F/F1 | unassigned | Same as #824. |
| #829 | R-F/F1 | unassigned | Same as #824. |
| #3385 | R-A/A1, R-A/A3 | unassigned | R-A/A1 and R-A/A3 are requirement groups without concrete P1 WPs. Capability consumption arch test exists but enforcement completeness gaps remain. |
| #3699 | R-A/A2 | unassigned | R-A/A2 is a requirement group, not a concrete P1 WP. Shared base delivered; remaining P6 consolidation work absorbed into A2 but no WP scheduled. |

**Key observation:** All 7 ABSORBED entries map to R-A (Requirement Group A) or R-F (Requirement Group F) from the backend-contract-layer task. Neither group has a concrete P1 WP assignment. These must be scheduled in P2-A9 or later phases.

## Snapshot Annotation

This disposition covers issues open at the 2026-06-10 program kickoff date.
Issues created after 2026-06-10 are tagged for P2-A9 rolling review and are
NOT individually dispositioned in this table.

To identify post-kickoff issues in scope clusters, run:
`gh issue list --repo TalonT-Org/AutoSkillit --json number,createdAt --limit 500 | jq '[.[] | select(.createdAt > "2026-06-10")]'`

## Verification Notes

1. **#3107 codebase check:** `CodexBackend.version()` confirmed at `src/autoskillit/execution/backends/codex.py:1077`. Classified SUPERSEDED with codebase state evidence.
2. **#3117 codebase check:** `triage_capable` field confirmed at `src/autoskillit/core/types/_type_backend.py:86`; consumed in `src/autoskillit/_llm_triage.py:107` and `src/autoskillit/server/_misc.py:211`. Classified SUPERSEDED with codebase state evidence.
3. **#2841 issue check:** Current title is "Ticket Grouper needs effort-based splitting for multi-file findings". Body describes audit skill Ticket Grouper improvements, not conformance tests. Classified INDEPENDENT per current content.
4. **#3699 codebase check:** `BackendCmdBuilderBase` ABC confirmed at `src/autoskillit/execution/backends/_backend_cmd_builder_base.py:64`. `CodexBackend` and `ClaudeCodeBackend` both inherit from it. Notes updated to reflect further consolidation progress.
5. **#3124 double-listing:** Appears in both §2 (string-to-capability) and §3 (conformance) but counted once in summary statistics.

## Summary Statistics

| Disposition | Count | Issues |
|-------------|-------|--------|
| SUPERSEDED | 37 | #3103, #3104, #3105, #3106, #3107, #3108, #3109, #3110, #3111, #3112, #3113, #3114, #3115, #3116, #3117, #3118, #3119, #3120, #3121, #3122, #3123, #3124, #3125, #3126, #3127, #3128, #3129, #3130, #3131, #3132, #3133, #3134, #3135, #3136, #3137, #3273, #3386 |
| ABSORBED | 2 | #3385 (R-A/A1, R-A/A3), #3699 (R-A/A2) |
| INDEPENDENT | 10 | #2841, #2936, #3297, #3638, #3676, #3756, #3781, #3836, #3876, #3877 |
| N/A | 1 | #3386 (PR number, not issue — included in SUPERSEDED count above) |
| **Total** | **50** | 35 string-to-capability (§2) + 4 conformance (§3, incl. #3124 shared with §2) + 1 builder-divergence + 2 guard-bypass + 8 D9 = 50 rows; 49 unique issues |

**Note on counts:** #3386 is both a PR number AND a disposition result (SUPERSEDED) — listed once in SUPERSEDED row. #3124 appears in §2 and §3 but counted once. The 37 SUPERSEDED = 35 entries in §2 minus #3124 (counted once in §3) = 34 string-to-capability + 1 (#3124 in conformance) + 1 (#3273) + 1 (#3386).