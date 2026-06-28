# Seed Disposition Table — P1-A7 Merged (WP1 + WP2 + WP3)

**Snapshot date:** 2026-06-28
**Seed coverage:** 75 issues across 3 work packages
**Source files:**
- `.autoskillit/temp/p1_a7_wp1_disposition_table.md` (WP1: Strategic & SessionLocator — 20 issues)
- `.autoskillit/temp/p1_a7_wp2_disposition_table.md` (WP2: String-to-Capability, Conformance, Builder-Divergence, Guard-Bypass, D9 — 49 unique issues)
- WP3 classifications embedded inline (this document)

**P2-A9 extension instruction:** P2-A9 must extend this table to cover all ~120 open meta-architecture issues; issues created after 2026-06-10 (program kickoff snapshot date) are out of scope for this seed.

## Disposition Table

| issue_number | title | classification | absorbing_requirement_or_wp | close_action |
|---|---|---|---|---|
| #23 | feat: Codex CLI backend support | superseded | — | close-with-reference |
| #55 | feat: introduce agent backend abstraction to decouple from Claude Code CLI | absorbed | R-A/A1 | map-to-wp |
| #772 | LLM provider abstraction layer for AutoSkillit headless sessions | independent | — | keep-open |
| #820 | feat: Open Code CLI backend support | absorbed | R-F/F1 | map-to-wp |
| #822 | Backend Protocol Foundation | absorbed | R-A/A1 | map-to-wp |
| #823 | Token Schema Normalization | absorbed | R-D/D8 | map-to-wp |
| #824 | CLI Backend Protocol Extraction | absorbed | R-F/F1 | map-to-wp |
| #827 | Codex CLI Backend | superseded | — | close-with-reference |
| #829 | Open Code CLI Backend — Deferred | absorbed | R-F/F1 | map-to-wp |
| #2453 | Author CanonicalTokenUsage IL-0 type | absorbed | R-D/D8 | map-to-wp |
| #2479 | Implement ClaudeResultParser | absorbed | R-D/D8 | map-to-wp |
| #2481 | Create backend registry and factory surface | absorbed | R-D/D8 | map-to-wp |
| #2671 | Add exit_code to ResultParser | absorbed | R-D/D8 | map-to-wp |
| #2841 | Ticket Grouper effort-based splitting | independent | — | keep-open |
| #2936 | Hook-based runtime enforcement for fleet config limits | independent | — | keep-open |
| #3103 | Extend BackendCapabilities with 18 fields | superseded | R-G/P1 | close-with-reference |
| #3104 | CodexBackend 18 capability kwargs | superseded | R-G/P1 | close-with-reference |
| #3105 | Protocol method stubs | superseded | R-G/P1 | close-with-reference |
| #3106 | ClaudeCodeBackend version/list_plugins/validate_skill_content | superseded | R-G/P1 | close-with-reference |
| #3107 | CodexBackend version() method | superseded | R-G/P1 | close-with-reference |
| #3108 | Backend Protocol completeness tests | superseded | R-G/P1 | close-with-reference |
| #3109 | _adapt_agent_result unified dispatch | superseded | R-G/P1 | close-with-reference |
| #3110 | triage_staleness signature → CodingAgentBackend | superseded | R-G/P1 | close-with-reference |
| #3111 | _version_snapshot.py Protocol dispatch | superseded | R-G/P1 | close-with-reference |
| #3112 | session_skills.py string elimination | superseded | R-G/P1 | close-with-reference |
| #3113 | validate_project_local_skill_dir backend-agnostic | superseded | R-G/P1 | close-with-reference |
| #3114 | channel_b_capable Codex log dispatch tests | superseded | R-G/P1 | close-with-reference |
| #3115 | recording.py capability replacement | superseded | R-G/P1 | close-with-reference |
| #3116 | Hoist backend + string comparison replacement | superseded | R-G/P1 | close-with-reference |
| #3117 | _apply_triage_gate capability lookup | superseded | R-G/P2 | close-with-reference |
| #3118 | Backend dispatch guard → capability | superseded | R-G/P2 | close-with-reference |
| #3119 | Capability-driven plugin guard | superseded | R-G/P2 | close-with-reference |
| #3120 | Refactor install() capability-driven | superseded | R-G/P2 | close-with-reference |
| #3121 | Doctor MCP checks → typed params | superseded | R-G/P2 | close-with-reference |
| #3122 | _check_codex_version → CodingAgentBackend param | superseded | R-G/P2 | close-with-reference |
| #3123 | AUTOSKILLIT_APPLICABLE_GUARDS constant | superseded | R-G/P2 | close-with-reference |
| #3124 | test_backend_compliance.py | superseded | R-G/P5 | close-with-reference |
| #3125 | Confirm P2-A8 arch tests present | superseded | R-G/P5 | close-with-reference |
| #3126 | Backend coherence arch tests | superseded | R-G/P5 | close-with-reference |
| #3127 | BackendCapabilities no-arg constructible | superseded | R-G/P5 | close-with-reference |
| #3128 | DeprecationWarning from commands.py shims | superseded | R-G/P5 | close-with-reference |
| #3129 | 'Adding a new backend' checklist | superseded | R-G/P5 | close-with-reference |
| #3130 | KNOWN_BACKEND_NAMES validation | superseded | R-G/P5 | close-with-reference |
| #3131 | BackendConventions + skills_subdir field | superseded | R-G/P6 | close-with-reference |
| #3132 | conventions on ClaudeCodeBackend | superseded | R-G/P6 | close-with-reference |
| #3133 | conventions on CodexBackend | superseded | R-G/P6 | close-with-reference |
| #3134 | conventions-based skills_subdir lookup | superseded | R-G/P6 | close-with-reference |
| #3135 | Delete _SKILLS_SUBDIR aliases | superseded | R-G/P6 | close-with-reference |
| #3136 | SessionIndexEntry.claude_code_log annotation fix | superseded | R-G/P6 | close-with-reference |
| #3137 | setup_session_dir stub + compliance tests | superseded | R-G/P6 | close-with-reference |
| #3273 | Backend-parametrized CLI tests | superseded | R-G | close-with-reference |
| #3297 | Codex config.toml TOML destructive overwrite | absorbed | R-B/B3a | map-to-wp |
| #3335 | Codex Config Schema Drift Immunity | absorbed | R-B/B3a | map-to-wp |
| #3385 | Codex Backend Architectural Immunity — Capability Consumption, Symmetric Env, and NotImplementedError Arch Tests | absorbed | R-A/A1, R-A/A3 | map-to-wp |
| #3386 | Standardize SKILL.md subagent spawn instructions | superseded | User-specified | close-with-reference |
| #3458 | Backend protocol contract for MCP env forwarding — architectural immunity against env stripping gaps | superseded | — | close-with-reference |
| #3638 | MCP tool timeout 120s kills long-running tools | absorbed | R-B/B3e | map-to-wp |
| #3676 | CODEX_MODEL_ALIASES stale model IDs | absorbed | R-D/D4 | map-to-wp |
| #3699 | Rectify: Codex Backend Systemic Builder Divergence — Shared Base, Closed Categorization, and Semantic Flag Validation | absorbed | R-A/A2 | map-to-wp |
| #3756 | Codex NDJSON Parser Schema Drift | absorbed | R-B/B3a | map-to-wp |
| #3781 | test_check codex_status misclassification | absorbed | R-B/B1 | map-to-wp |
| #3836 | run_skill codex_status misclassification | absorbed | R-B/B1 | map-to-wp |
| #3876 | .git read-only sandbox (worktree broken under Codex) | independent | — | keep-open |
| #3877 | Expired /dev/shm SKILL.md paths | absorbed | R-D/D2 + R-D/D3b | map-to-wp |
| #3922 | T4-P3-A1-WP1 Make ClaudeSessionLocator and CodexSessionLocator nominally subclass SessionLocator and resolve the codex_home Protocol drift in CodexSessionLocator | superseded | — | close-with-reference |
| #3923 | T4-P3-A3-WP1 Extend SessionLocator Protocol with project_log_dir and implement it on both ClaudeSessionLocator and CodexSessionLocator | superseded | — | close-with-reference |
| #3924 | T4-P3-A4-WP1 Extend SessionLocator Protocol with session_log_path, implement on both backends, and add compliance tests | superseded | — | close-with-reference |
| #3925 | T4-P3-A5-WP1 Eliminate all claude_code_project_dir() and claude_code_log_path() call sites from fleet/_api.py via Protocol dispatch | superseded | — | close-with-reference |
| #3926 | T4-P3-A6-WP1 Eliminate claude_code_project_dir() call in run_skill() via Protocol dispatch; prove routing with unit test. | superseded | — | close-with-reference |
| #3927 | T4-P3-A7-WP1 Replace Claude-specific path computation in _headless_helpers.py with Protocol dispatch and update all dependent tests | superseded | — | close-with-reference |
| #3929 | T4-P3-A9-WP1 Decouple _session_picker.py from claude_code_project_dir() by accepting project_log_dir as a parameter. | superseded | — | close-with-reference |
| #3930 | T4-P3-A10-WP1 Replace vestigial OSError guard with Protocol dispatch and prove routing via smoke test. | superseded | — | close-with-reference |
| #3932 | T4-P3-A12-WP1 Ensure every backend exposes project_log_dir on its SessionLocator. | closed | — | already-closed |
| #3971 | Fleet dispatch does not enforce backend_supports_git_write authority contract | absorbed | R-A/A4 | map-to-wp |
| #3993 | Codex backend cache_write_tokens=None crashes token summary | closed | R-D/D8 | already-closed |

## Summary Statistics

### Classification

| classification | count |
|---|---|
| superseded | 51 |
| absorbed | 20 |
| independent | 3 |
| closed | 1 |
| **Total** | **75** |

### Close Action

| close_action | count |
|---|---|
| close-with-reference | 51 |
| map-to-wp | 20 |
| keep-open | 3 |
| already-closed | 1 |
| **Total** | **75** |

### Cross-References

| issue_number | title | classification | absorbing_requirement_or_wp | close_action |
|---|---|---|---|---|
| #3297 | Codex config.toml TOML destructive overwrite | absorbed | R-B/B3a | map-to-wp |
| #3638 | MCP tool timeout 120s kills long-running tools | absorbed | R-B/B3e | map-to-wp |
| #3335 | Codex Config Schema Drift Immunity | absorbed | R-B/B3a | map-to-wp |
| #3877 | Expired /dev/shm SKILL.md paths | absorbed | R-D/D2 + R-D/D3b | map-to-wp |
| #3876 | .git read-only sandbox (worktree broken under Codex) | independent | — | keep-open |
| #3676 | CODEX_MODEL_ALIASES stale model IDs | absorbed | R-D/D4 | map-to-wp |
| #3836 | run_skill codex_status misclassification | absorbed | R-B/B1 | map-to-wp |
| #3781 | test_check codex_status misclassification | absorbed | R-B/B1 | map-to-wp |
| #3971 | Fleet dispatch does not enforce backend_supports_git_write authority contract | absorbed | R-A/A4 | map-to-wp |
| #3993 | Codex backend cache_write_tokens=None crashes token summary | closed | R-D/D8 | already-closed |
| #823 | Token Schema Normalization | absorbed | R-D/D8 | map-to-wp |
| #2453 | Author CanonicalTokenUsage IL-0 type | absorbed | R-D/D8 | map-to-wp |
| #2671 | Add exit_code to ResultParser | absorbed | R-D/D8 | map-to-wp |