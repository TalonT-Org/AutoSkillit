# P1-A7-WP1 Disposition Table — Strategic & SessionLocator Cluster

Generated: 2026-06-11
Branch: impl-4004-20260611-162127

## Consolidated Disposition Table

| Issue # | Title | State | Disposition | Reason | Mapped WP / Label | Codebase Evidence |
|---------|-------|-------|-------------|--------|-------------------|-------------------|
| #23 | feat: Codex CLI backend support | OPEN | SUPERSEDED | Codex backend live and registered; full `CodingAgentBackend` Protocol implementation | — | `BACKEND_REGISTRY["codex"]` in `execution/backends/__init__.py:45-48`; `CodexBackend` in `execution/backends/codex.py` |
| #55 | feat: introduce agent backend abstraction to decouple from Claude Code CLI | OPEN | ABSORBED | Backend abstraction introduced (`CodingAgentBackend` Protocol) but enforcement completeness gaps remain — 8 forward-declared capability fields without production consumers | R-A/A1 | `CodingAgentBackend` Protocol in `core/types/_type_protocols_backend.py`; 8 fields in `_FORWARD_DECLARED` in `tests/arch/test_capability_consumption.py:26` |
| #772 | LLM provider abstraction layer for AutoSkillit headless sessions | OPEN | INDEPENDENT | Non-goal per task §4: "Model-quality parity between providers; LLM provider abstraction beyond what token accounting requires" | Independent | N/A |
| #820 | feat: Open Code CLI backend support | OPEN | ABSORBED | Paper-backend exercise (R-F/F1) validates third-backend readiness; Open Code backend not yet implemented | R-F/F1 | No paper-backend implementation exists yet; deferred per `defer` label |
| #822 | Backend Protocol Foundation | OPEN | ABSORBED | Protocol foundation established but enforcement completeness gaps remain — forward-declared fields need disposition under A1 pass | R-A/A1 | `CodingAgentBackend` Protocol exists; `BackendCapabilities` has 8 `_FORWARD_DECLARED` fields in `test_capability_consumption.py` |
| #824 | CLI Backend Protocol Extraction | OPEN | ABSORBED | Protocol extracted (`CodingAgentBackend`, `SessionLocator`) but third-backend exercise needed to validate extraction completeness | R-F/F1 | Protocol exists in `core/types/_type_protocols_backend.py`; paper-backend exercise will validate extensibility |
| #827 | Codex CLI Backend | OPEN | SUPERSEDED | Codex backend live, registered, and fully functional | — | `CodexBackend` registered in `BACKEND_REGISTRY` at `execution/backends/__init__.py:47` |
| #829 | Open Code CLI Backend — Deferred | OPEN | ABSORBED | Deferred third-backend; paper-backend exercise must walk every Protocol method including env forwarding | R-F/F1 | No paper-backend implementation; deferred per `defer` label |
| #3385 | Codex Backend Architectural Immunity — Capability Consumption, Symmetric Env, and NotImplementedError Arch Tests | OPEN | ABSORBED | Arch tests exist (capability consumption, coherence, name-bypass) but enforcement completeness gaps remain — 8 forward-declared fields without production consumers; `CodexBackend.build_inspector_cmd()` raises `RuntimeError` | R-A/A1, R-A/A3 | `test_capability_consumption.py` has 8 `_FORWARD_DECLARED` fields; `CodexBackend.build_inspector_cmd()` at `codex.py:1052-1053` raises `RuntimeError` |
| #3458 | Backend protocol contract for MCP env forwarding — architectural immunity against env stripping gaps | OPEN | SUPERSEDED | MCP env-forward contract established: `CODEX_MCP_ENV_FORWARD_VARS` (6 vars) defined, assigned to `mcp_env_forward_vars` capability, arch test validates injection across 5 builder commands | — | `CODEX_MCP_ENV_FORWARD_VARS` in `core/types/_type_constants_env.py:147`; `test_mcp_env_forward_coverage.py` validates 5 builders |
| #3699 | Rectify: Codex Backend Systemic Builder Divergence — Shared Base, Closed Categorization, and Semantic Flag Validation | OPEN | ABSORBED | 4 parallel codex builders exist without shared base; copy-paste gap is the dominant failure mode (7 of 16+ Rectify commits); shared builder core not yet implemented | R-A/A2 | 4 builders in `codex.py`; `test_codex_exec_builder_invariants.py` covers structural invariants but shared base absent |
| #3922 | T4-P3-A1-WP1 Make ClaudeSessionLocator and CodexSessionLocator nominally subclass SessionLocator and resolve the codex_home Protocol drift in CodexSessionLocator | OPEN | SUPERSEDED | Both locators nominally subclass `SessionLocator`; Protocol drift resolved — all 3 methods implemented on both | — | `ClaudeSessionLocator(SessionLocator)` at `claude.py:99`; `CodexSessionLocator(SessionLocator)` at `codex.py:241` |
| #3923 | T4-P3-A3-WP1 Extend SessionLocator Protocol with project_log_dir and implement it on both ClaudeSessionLocator and CodexSessionLocator | OPEN | SUPERSEDED | `project_log_dir` method exists on Protocol and both implementations | — | `_type_protocols_backend.py:70`; `claude.py:114`; `codex.py:368` |
| #3924 | T4-P3-A4-WP1 Extend SessionLocator Protocol with session_log_path, implement on both backends, and add compliance tests | OPEN | SUPERSEDED | `session_log_path` method exists on Protocol and both implementations | — | `_type_protocols_backend.py:78`; `claude.py:117`; `codex.py:371` |
| #3925 | T4-P3-A5-WP1 Eliminate all claude_code_project_dir() and claude_code_log_path() call sites from fleet/_api.py via Protocol dispatch | OPEN | SUPERSEDED | `fleet/_api.py` uses `tool_ctx.backend.session_locator()` — no direct `claude_code_project_dir` calls | — | `fleet/_api.py:552-556` uses `session_locator()`; `project_log_dir` at line 876 |
| #3926 | T4-P3-A6-WP1 Eliminate claude_code_project_dir() call in run_skill() via Protocol dispatch; prove routing with unit test. | OPEN | SUPERSEDED | `run_skill()` uses `tool_ctx.backend.session_locator().project_log_dir()` | — | `tools_execution.py:989` |
| #3927 | T4-P3-A7-WP1 Replace Claude-specific path computation in _headless_helpers.py with Protocol dispatch and update all dependent tests | OPEN | SUPERSEDED | `_headless_helpers.py` uses `backend.session_locator().project_log_dir(cwd)` — no direct claude path calls | — | `_headless_helpers.py:54` `_session_log_dir()` function |
| #3929 | T4-P3-A9-WP1 Decouple _session_picker.py from claude_code_project_dir() by accepting project_log_dir as a parameter. | OPEN | SUPERSEDED | `pick_session(session_type, project_dir, project_log_dir)` receives `project_log_dir` as parameter — fully decoupled | — | `_session_picker.py:19` function signature |
| #3930 | T4-P3-A10-WP1 Replace vestigial OSError guard with Protocol dispatch and prove routing via smoke test. | OPEN | SUPERSEDED | Protocol dispatch is the routing mechanism throughout the codebase; `BACKEND_REGISTRY` provides dispatch, all call sites use `backend.session_locator()` | — | `BACKEND_REGISTRY` in `execution/backends/__init__.py:45-48`; no vestigial OSError guards remain in call sites |
| #3932 | T4-P3-A12-WP1 Ensure every backend exposes project_log_dir on its SessionLocator. | CLOSED | SUPERSEDED | Both backends expose `project_log_dir` via `SessionLocator` subclass implementations | — | `ClaudeSessionLocator.project_log_dir` at `claude.py:114`; `CodexSessionLocator.project_log_dir` at `codex.py:368` |

## Codebase-State Verification Notes

1. **All Protocol methods implemented**: SessionLocator Protocol defines 3 methods (`locate_session`, `project_log_dir`, `session_log_path`). Both `ClaudeSessionLocator` and `CodexSessionLocator` implement all 3. Verified at `_type_protocols_backend.py:65-78`.

2. **Both locators nominally subclass SessionLocator**: `ClaudeSessionLocator(SessionLocator)` at `claude.py:99` and `CodexSessionLocator(SessionLocator)` at `codex.py:241`.

3. **All call-site migrations complete**: No production code outside `ClaudeSessionLocator` itself calls `claude_code_project_dir` or `claude_code_log_path` directly. Verified call sites: `fleet/_api.py:552`, `_headless_helpers.py:54`, `tools_execution.py:989`, `_session_picker.py:19`.

4. **Issues closable immediately**: #3922, #3923, #3924, #3925, #3926, #3927, #3929, #3930 — all have codebase evidence confirming the work is complete on the current branch. P2-A9 can close these after verification.

5. **#3932 already closed**: Confirmed closed in GitHub. Codebase evidence confirms fix is live.

6. **#3922 closable but note scope**: The nominal subclassing is done and Protocol drift is resolved. The umbrella-level enforcement work (forward-declared fields) is tracked separately under #3385 (ABSORBED → R-A/A1).

7. **Conflict with issues_catalog.md**: The catalog noted SessionLocator items as "planned" but the current branch has them fully implemented. The disposition table reflects the actual codebase state.

## Conflicts with Prior Analysis

1. **SessionLocator series status**: The `issues_catalog.md` supersession analysis characterized SessionLocator series items (#3922–#3930) as planned/pending. The current branch has all of them fully implemented. Disposition table reflects actual codebase state, not the catalog's earlier assessment.

2. **#3932 state**: The issue acceptance criteria for this WP expected #3932 to be open with a note about work being complete. In fact, #3932 is already CLOSED in GitHub. The table records the actual state (CLOSED).

3. **#3925 state**: The original plan draft incorrectly noted #3925 as "SUPERSEDED (CLOSED)". #3925 is actually OPEN in GitHub (the work IS complete on the branch but the issue hasn't been closed yet). The table records the actual state (OPEN).

## Summary Statistics

| Disposition | Count | Issues |
|-------------|-------|--------|
| SUPERSEDED | 12 | #23, #827, #3458, #3922, #3923, #3924, #3925, #3926, #3927, #3929, #3930, #3932 |
| ABSORBED | 7 | #55 (R-A/A1), #822 (R-A/A1), #824 (R-F/F1), #820 (R-F/F1), #829 (R-F/F1), #3385 (R-A/A1, R-A/A3), #3699 (R-A/A2) |
| INDEPENDENT | 1 | #772 |
| **Total** | **20** | |
