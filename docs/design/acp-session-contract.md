# Design Spec: ACP Session Contract Alignment

**Status:** Draft
**Date:** 2026-06-27
**Issue:** [#4053](https://github.com/TalonT-Org/AutoSkillit/issues/4053)
**Normative reference for:** P6-A3-WP1

## Overview

This document provides the authoritative mapping between the autoskillit
`CodingAgentBackend` session lifecycle and ACP (Agent Communication Protocol)
session method semantics. It serves as the normative reference for P6-A3-WP1.

Four sections cover:

1. **Lifecycle Mapping** — per-method mapping of `CodingAgentBackend` protocol
   methods to ACP session methods for `ClaudeCodeBackend` and `CodexBackend`.
2. **Recovery Ladder** — mapping of the 16 `RetryReason` enum values to ACP
   session rungs (`session/resume`, `session/load`, `session/new`) and
   terminal/wait-and-retry handling, plus the contract-nudge mechanism.
3. **Capabilities Translation** — field-by-field categorization of all 47
   `BackendCapabilities` fields into ACP-mappable, autoskillit-local extension,
   and forward-declared buckets (validated against `_FORWARD_DECLARED` in
   `tests/arch/test_capability_consumption.py`).
4. **Codex Shim Deviations** — the seven categories where the Codex backend
   diverges from Claude Code semantics, including all eight discard sites.

The normative contract preserved by this alignment is that the orchestrator
selects an executor action (resume / load / new) based on the
`RetryReason` emitted by `_build_skill_result` (`execution/headless/_headless_result.py`),
which feeds the recipe-level `on_context_limit` / `on_failure` / `on_rate_limit`
routing fields (`recipe/schema.py` lines 119–121).

---

## Section 1: Lifecycle Mapping

The `CodingAgentBackend` protocol
(`src/autoskillit/core/types/_type_protocols_backend.py`) defines 26 methods.
Its four sub-protocols define another 8 (`StreamParser` 1, `ResultParser` 2,
`EnvPolicy` 1, and `SessionLocator` 4), for 34 documented protocol methods.
The per-method table below maps each backend protocol
method to its ACP session method analogue for both `ClaudeCodeBackend` and
`CodexBackend`, with explicit notes where the Codex implementation deviates.

| Protocol method | ACP analogue | Claude Code | Codex | Codex deviation notes |
|---|---|---|---|---|
| `name` | (identity) | `"claude"` | `"codex"` | — |
| `capabilities` | (capability declaration) | `CLAUDE_CODE_CAPABILITIES` constant | `CodexBackend.capabilities` property | Codex constructs the capabilities instance on every access (no module-level `CODEX_CAPABILITIES` constant). |
| `conventions` | (backend conventions) | Returns `BackendConventions` | Returns `BackendConventions` | — |
| `build_cmd` | `session/new` (headless one-shot) | Builds `CmdSpec` invoking the `claude` binary | Builds `CmdSpec` invoking the `codex` binary | — |
| `build_skill_session_cmd` | `session/new` (skill session; optional resume via `config.resume_session_id`) | Builds `CmdSpec` for skill execution with optional resume | Builds `CmdSpec`; **discards `plugin_source`, `output_format`, `exit_after_stop_delay_ms`** (lines 698–701, `# noqa: F841`) | Codex has no `--plugin-dir`; `--json` is unconditional; `exit_after_stop_delay_ms` is Claude-only. |
| `build_resume_cmd` | `session/resume` | Uses `--resume <session_id>` flag | Uses positional `resume <session_id>` subcommand (`CodexFlags.RESUME_SUBCOMMAND = "resume"`, lines 978–979); validates non-empty `resume_session_id` (raises `ValueError`) | Positional subcommand vs. flag; input validation. |
| `build_interactive_cmd` | `session/new` or `session/resume` (via `ResumeSpec`: `NoResume | BareResume | NamedResume`) | System prompt as `--append-system-prompt <value>` (only applied on `NoResume`) | System prompt as `-c developer_instructions=<value>` (line 931; same `NoResume` restriction); **`tools` arg silently discarded with `logger.warning("codex_tools_ignored")`** at lines 909–913 | `CodexFlags.CONFIG_OVERRIDE = "-c"`; `tools` arg discarded with warning rather than error. |
| `build_food_truck_cmd` | `session/new` (orchestrator-level session, L2) | Builds `CmdSpec` for food-truck orchestrator | Builds `CmdSpec`; **discards `plugin_source`, `output_format`, `exit_after_stop_delay_ms`** (lines 821–823, `# noqa: F841`); sandbox is `read-only` (not `workspace-write`); no `--tools AskUserQuestion` | Same three discard reasons as `build_skill_session_cmd`; sandbox policy differs; no AskUserQuestion tool. |
| `build_inspector_cmd` | No ACP analogue (lightweight probe, not a session) | Raises `CapabilityNotSupportedError` (`inspector_capable=False` in `CLAUDE_CODE_CAPABILITIES`; unreachable `AssertionError` stub at line 875 is dead code) | Raises `CapabilityNotSupportedError` when `inspector_capable=False` | — (both backends gate via `inspector_capable=False`) |
| `setup_session_dir` | ACP pre-session initialization | No-op | Substantial setup: copies `config.toml`, symlinks `auth.json` + `.env` + `sessions/`, generates agent TOMLs, materializes profile skills (lines 1021–1072) | Codex: rich setup with multiple failure-logged steps. |
| `validate_session_layout` | ACP session validation (post-setup) | Validates session directory layout | Validates session directory layout (includes `required_session_files={"config.toml"}`) | — |
| `validate_interactive_invocation` | ACP launch admission | Validates the finalized interactive `CmdSpec` | Validates the exact finalized project-cwd command and immutable generated config before an attempt view exists | Codex validates config and reserved home/state overrides before storage mutation. |
| `validate_skill_content` | ACP skill-content validation | YAML frontmatter validation (`required_skill_fields={"name", "description"}`) | Returns `[]` unconditionally (no frontmatter requirement) | Codex: structural discard — entire validation is a no-op. |
| `stream_parser` | ACP event stream consumption | Returns a `StreamParser` for stdout/JSONL events | Returns a `StreamParser` for NDJSON events (`thread.started`, `turn.completed`, `turn.failed`) | Codex events are NDJSON with `thread_id` resolution for `CodexSessionLocator`. |
| `result_parser` | ACP event aggregation | Returns a `ResultParser` aggregating events into `AgentSessionResult` | Returns a `ResultParser` aggregating Codex events; populated `jsonl_context_exhausted` when `error_code == CODEX_CONTEXT_EXHAUSTION_MARKER` (lines 105–108 of `_headless_evidence.py`) | Codex surfaces context-exhaustion via `error_code` rather than API `needs_retry`. |
| `env_policy` | ACP environment contract | Returns `EnvPolicy` for subprocess env (injects MCP env-forward vars per Env Forwarding Contract) | Returns `EnvPolicy`; env-denylist prefixes via `CODEX_ENV_PREFIX_DENYLIST` | Codex applies a denylist; Claude Code does not. |
| `session_locator` | ACP session discovery / `session/load` | Returns a `SessionLocator` resolving session log dirs from Channel B JSONL | Returns `CodexSessionLocator`; `list_sessions` reads the derived typed index while `locate_session` searches canonical active/archive stores and validated active views by real `thread_id` | Listing is read-only; explicit recovery, not listing, rebuilds the derived index. |
| `write_tool_names` | (write-detection contract) | `frozenset({"Write", "Edit", "Bash", "apply_patch"})` | `frozenset({"apply_patch", "Bash", "run_cmd"})` | Different write-tool vocabularies; Codex uses `apply_patch` + `run_cmd`. |
| `binary_name` | (process identity) | `"claude"` | `"codex"` | — |
| `version` | (capability introspection) | Returns backend version string | Returns backend version string | — |
| `list_plugins` | (plugin enumeration) | Lists known plugins as dicts | Lists known plugins as dicts | — |
| `ensure_pre_launch` | (pre-flight checks) | Pre-launch checks/setup | Pre-launch checks/setup | — |
| `recover_cook_history` | ACP durable-session recovery | No-op | Explicitly recovers safely owned attempt views, then rebuilds the derived index before bare-resume selection | Recovery is never hidden in `SessionLocator.list_sessions`. |
| `cook_session_context` | ACP attempt ownership | Null context | Acquires the per-attempt view/thread ownership contract and returns `CookSessionHandle` | Context exit requires durable spawn/reap proof before promotion. |
| `translate_model` | (model alias resolution) | Translates canonical model name to backend-specific name | Translates canonical model name to backend-specific name | — |
| `model_config_overrides` | (model-specific CLI overrides) | Returns CLI overrides tuple for a given model | Returns CLI overrides tuple for a given model | — |

### Interactive lifecycle data and ownership

The backend-neutral records separate user-visible identity from launch
ownership:

| Contract | Meaning |
|---|---|
| `SessionSummary` | Read-only picker record. `session_id` is the backend's resumable ID, optional `launch_id` joins the AutoSkillit registry, `cwd` is the project discriminator, and `session_type_hint` is used only when no registry classification exists. |
| `ManagedSessionHome` | One logical interactive launch: immutable `launch_id`, generated home, separately typed `skills_dir`, and inherited generated-home lease descriptors in `pass_fds`. Its context owns transactional setup, exactly-once verified cleanup, and unconditional lease release across all reload attempts. |
| `CookSessionHandle` | One attempt view: store-derived `view_id`, inherited view/thread descriptors, and one-shot `record_spawn(pid, pgid)` / `record_reaped(pid, pgid)` callbacks. Reap proof is recorded only after the complete process group is empty and the direct child is reaped. |
| `HookTrustPolicy` | `REVIEW_EACH_SESSION` emits no hook-trust bypass for interactive fresh, named-resume, bare-resume, or reload commands. Automated skill and food-truck builders retain their explicit bypass. |

Identifier scopes are intentionally disjoint: `launch_id` spans one `cook()`
and its reload loop; `attempt` increments for each child launch; `view_id` is
derived once by storage from `(launch_id, attempt)`; and Codex `thread_id` is
the backend resume identity. None may be substituted for another.

Ownership is similarly layered. The managed-home context owns generated-home
materialization and its lease. The prelaunch transaction owns synchronized MCP
and hook mutation plus the immutable config snapshot. `CodexSessionStore`
owns inert rollout targets, active views, manifests, locks, promotion,
recovery, and the rebuildable listing index; SQLite stays disposable inside
the generated home. The cook process owner alone owns PID/PGID creation,
terminal transfer, TERM/KILL cleanup, direct-child reap, and the callbacks
that allow storage finalization. The child command's finalized `cwd` remains
the canonical project directory; generated-home paths are reserved only for
configuration and state.

### Method-grouping rationale

The protocol methods partition into five behavioral clusters:

1. **Session creation** (`build_cmd`, `build_skill_session_cmd`, `build_interactive_cmd`,
   `build_food_truck_cmd`) — map to ACP `session/new`.
2. **Session continuation** (`build_resume_cmd`) — maps to ACP `session/resume`.
3. **Session discovery** (`session_locator`) — maps to ACP `session/load`.
4. **Validation** (`validate_session_layout`, `validate_skill_content`,
   `ensure_pre_launch`) — maps to ACP session/state validation pre/post hooks.
5. **Capability introspection** (`name`, `capabilities`, `conventions`, `version`,
   `list_plugins`, `translate_model`, `model_config_overrides`) — backend metadata
   that the orchestrator consumes before invoking ACP methods.

`build_inspector_cmd` is a non-session probe (Health Inspector per issue #3533)
and has no ACP analogue. Both backends raise `CapabilityNotSupportedError`
(`inspector_capable=False` in both `CLAUDE_CODE_CAPABILITIES` and
`CodexBackend.capabilities`); an unreachable `AssertionError` stub remains in
`ClaudeCodeBackend.build_inspector_cmd` (line 875) as a defensive guard.

---

## Section 2: Recovery Ladder

The recovery ladder maps each of the 16 `RetryReason` enum values
(`src/autoskillit/core/types/_type_enums.py`, lines 44–64) to one of three ACP
session rungs — `session/resume`, `session/load`, `session/new` — or to a
terminal/wait-and-retry classification.

The route is encoded by the `RetryReason` emitted from `_build_skill_result`
(`src/autoskillit/execution/headless/_headless_result.py`) and consumed by the
recipe's `on_context_limit` / `on_failure` / `on_rate_limit` fields
(`src/autoskillit/recipe/schema.py`, lines 119–121). The FSM at
`src/autoskillit/execution/session/_retry_fsm.py` produces the raw reason;
`_headless_result.py` applies post-classification overrides that promote
specific infra-classification signals (e.g. API errors → `RESUME`, rate limits
→ `RATE_LIMITED`).

### 2.1 RetryReason → ACP rung mapping

| RetryReason | ACP Rung | Routing Callback | Notes |
|---|---|---|---|
| `RESUME` | `session/resume` | `on_context_limit` | API-level `needs_retry`; Claude `ERROR_MAX_TURNS` / `_is_context_exhausted()`; Codex `jsonl_context_exhausted` (`_retry_fsm.py:83`). API error override at `_headless_result.py:566` promotes API errors here. |
| `DRAIN_RACE` | `session/resume` | `on_context_limit` | Channel-confirmed completion, stdout not fully flushed before kill (`_session_outcome.py:241` dead-end guard). |
| `COMPLETED_NO_FLUSH` | `session/resume` | `on_context_limit` | `EMPTY_OUTPUT` + write evidence; stdout absent (not merely unflushed). Audit field at `_headless_result.py:647–648` and gate at lines 782–786. |
| `THINKING_STALL` | `session/resume` or `session/new` | `on_context_limit` if `lifespan_started`, else `on_failure` | Final turn: thinking blocks only, no text or tool output (`_retry_fsm.py:114`). Partial progress determines rung. |
| `CONTRACT_RECOVERY` | `session/resume` or `session/new` | `on_context_limit` if `has_progress_evidence`, else `on_failure` | Marker present + write evidence — omission not structural (`_headless_result.py:754–758`). After nudge attempt fails. |
| `IDLE_STALL` | `session/resume` | `on_context_limit` | Stdout idle watchdog kill — session may have partial progress (`_headless_result.py:354`). |
| `EARLY_STOP` | `session/new` | `on_failure` | Model stopped before completion marker (`_retry_fsm.py:160`). |
| `EMPTY_OUTPUT` | `session/new` | `on_failure` | `NATURAL_EXIT` + rc=0 + no output; no write evidence at exit (`_retry_fsm.py:118`). |
| `ZERO_WRITES` | `session/new` | `on_failure` | Silent degradation — success subtype but no implementation evidence (`_headless_result.py:242, 622, 779`). |
| `PATH_CONTAMINATION` | `session/new` | `on_failure` | CWD boundary violation, not a context limit (`_headless_result.py:738`). |
| `CLONE_CONTAMINATION` | `session/new` | `on_failure` | Session wrote to clone CWD; not a context limit. |
| `STALE` | `session/load` | Provider fallback loop | Transient stale session — retry from scratch; not a context limit (`_headless_result.py:251`). |
| `BUDGET_EXHAUSTED` | (terminal) | Provider fallback check | Consecutive retry budget exceeded; no further retry. |
| `RATE_LIMITED` | (wait-and-retry) | `on_rate_limit` | Transient HTTP 429 or rate-limit pattern — wait then same rung (`_headless_result.py:578` override). |
| `CANCELLED` | (terminal) | N/A | Transport teardown; no recovery. |
| `NONE` | (no retry) | N/A | Success — no recovery needed. |

### 2.2 ACP rung semantics

- **`session/resume`** — Continue from checkpoint. The recovery step invokes
  `backend.build_resume_cmd(resume_session_id=..., prompt=..., ...)` to target
  the same ACP session. Both backends implement this rung via positional
  (`Codex`) or flag (`Claude Code`) resume semantics; see Section 4.3.
- **`session/load`** — Reload from persisted state. Triggered when the prior
  session's persisted state is stale (provider fallback loop). The orchestrator
  locates the session via `backend.session_locator()` and re-invokes the
  appropriate ACP method with the resolved session ID.
- **`session/new`** — Start fresh. The recovery step invokes
  `backend.build_skill_session_cmd` or `backend.build_cmd` with no resume ID;
  no prior session state is consulted.

### 2.3 Contract nudge mechanism

The contract nudge (`src/autoskillit/execution/headless/_headless_recovery.py`,
`_attempt_contract_nudge`, lines 208–341) is a lightweight ACP `session/resume`
that recovers from missing structured tokens or completion markers:

- **Trigger conditions** — invoked from `_headless_execute.py:437` only when
  `skill_result.retry_reason in (RetryReason.CONTRACT_RECOVERY, RetryReason.EARLY_STOP)`.
- **Pre-flight gate** — returns `None` immediately if `backend` is `None`, if
  `backend.capabilities.session_resume_capable` is `False`, or if
  `result_parser` is `None` (lines 234–237). This is the **only ACP-rung check**
  in the nudge: a backend that cannot resume cannot use this path.
- **ACP rung invocation** — calls `backend.build_resume_cmd(...)` at line 264
  with a targeted prompt (`_NUDGE_TIMEOUT=60.0`); validated by
  `assert_headless_cmd(spec)` at line 270.
- **Branching** —
  - `EARLY_STOP` (line 239): emits a "completion marker only" prompt; success
    requires `completion_marker in nudge_session.output` AND patterns satisfied.
  - Default `CONTRACT_RECOVERY`: calls `_extract_missing_token_hints` (line 248)
    to look up `Write`/`Edit` `file_path` values from `subprocess_result.stdout`
    for each missing path-capture pattern; returns `None` if no hints (line 252).
- **Token accounting** — on success, returns
  `dataclasses.replace(skill_result, ..., token_usage=_merge_token_usage(skill_result.token_usage, _nudge_usage))`
  via `_merge_token_usage` (lines 314, 340, 345–367). `_merge_token_usage`
  reconciles canonical (`input_tokens`, `output_tokens`, `cache_write_tokens`,
  `cache_read_tokens`) and legacy (`cache_creation_input_tokens`,
  `cache_read_input_tokens`) forms via `_CANONICAL_TO_LEGACY` (lines 41–46).

The contract nudge exclusively targets `session/resume`; it never invokes
`session/load` or `session/new`.

---

## Section 3: Capabilities Translation

`BackendCapabilities` (`src/autoskillit/core/types/_type_backend.py`,
frozen dataclass, 47 fields total) declares feature flags the orchestrator
consumes when selecting an ACP rung or backend-specific code path. Each field
falls into one of three categories:

- **ACP-Mappable** — has a direct or close analogue in ACP session capabilities.
- **autoskillit-Local Extension** — no ACP analogue; required for autoskillit's
  extended contract (skill discovery, write-guard, hook config, etc.).
- **Forward-Declared** — declared for future use; no current production consumer
  outside the exemption set. Membership is validated against `_FORWARD_DECLARED`
  in `tests/arch/test_capability_consumption.py`.

The mechanically verified counts are **17 ACP-Mappable + 7 Forward-Declared
+ 23 autoskillit-Local = 47 total**. They supersede the earlier 41-field
snapshot.

### 3.1 Category 1: ACP-Mappable (17 fields)

| Field | ACP analogue |
|---|---|
| `channel_b_capable` | ACP event stream side-channel for session confirmation |
| `pty_required` | ACP subprocess allocation model (pseudo-TTY) |
| `session_resume_capable` | ACP `session/resume` support — gates `_attempt_contract_nudge` (see Section 2.3) |
| `skill_injection_capable` | ACP capability injection (skill registration on session start) |
| `supports_claude_format_stdout` | ACP response format (Claude `stream-json` vs Codex NDJSON) |
| `exit_code_is_terminal` | ACP terminal signal semantics (`exit_code_is_terminal=True` for Codex; `False` for Claude Code) |
| `mcp_config_capable` | ACP MCP config wiring (Claude: `False`; Codex: `True`) |
| `food_truck_capable` | ACP orchestrator-level sessions (L2) |
| `completion_record_types` | ACP completion event types (`frozenset({"result"})` for Claude; `frozenset({"turn.completed", "turn.failed", "error"})` for Codex) |
| `session_record_types` | ACP activity event types (`frozenset({"assistant"})` for Claude; `frozenset({"item.completed"})` for Codex) |
| `triage_capable` | ACP lightweight probe capability |
| `supports_context_exhaustion_detection` | ACP context-exhaustion signal (`jsonl_context_exhausted` adapter on Codex) |
| `process_name` | ACP process identity (subprocess name for `CodexSessionLocator` / ClaudeCodeSessionLocator) |
| `process_name_aliases` | ACP process identity aliases (psutil/grep matching) |
| `replay_capable` | ACP scenario replay (recording → replay) |
| `record_capable` | ACP scenario recording |
| `inspector_capable` | ACP health monitoring callback (Health Inspector per issue #3533) |

### 3.2 Category 2: autoskillit-Local Extension (23 fields)

| Field | autoskillit-specific contract |
|---|---|
| `supports_tool_list_changed` | autoskillit kitchen reveal timing (tool list notification) |
| `required_skill_fields` | autoskillit `SKILL.md` validation (`frozenset({"name", "description"})`) |
| `applicable_guards` | autoskillit guard script enforcement (Claude: `{"skill_load_guard"}`; Codex: `{"write_guard"}`) |
| `write_guard_tool_names` | autoskillit write-guard system (Claude: `{"Write", "Edit", "Bash", "apply_patch"}`; Codex: `{"apply_patch", "Bash", "run_cmd"}`) |
| `env_denylist_prefixes` | autoskillit env scrubbing (Codex applies `CODEX_ENV_PREFIX_DENYLIST`; Claude: `()`) |
| `version_check_command` | autoskillit doctor version validation (Claude: `"claude --version"`; Codex: `"codex --version"`) |
| `skills_subdir` | autoskillit session skill directory layout (Claude: `".claude/skills"`; Codex: `"skills"`) |
| `hook_config_format` | autoskillit hook config (Claude: `""`; Codex: `"toml_nested"`) |
| `write_detection_strategy` | autoskillit write detection (Claude: `"tool_names"`; Codex: `"file_changes"`) |
| `default_skill_sandbox_mode` | autoskillit sandbox policy (Claude: `""`; Codex: `"workspace-write"`) |
| `anthropic_provider_capable` | autoskillit provider-override routing (Claude only) |
| `plugin_install_capable` | autoskillit plugin management (Claude only) |
| `supports_context_window_suffix` | autoskillit model alias handling (Claude: `True`; Codex: `False`) |
| `has_unguarded_filesystem_access` | autoskillit prompt supplement gating (Claude: `False`; Codex: `True`) |
| `git_metadata_writable` | autoskillit git metadata safety (Claude: `True`; Codex: `False` — codex-rs sandbox) |
| `skill_sigil` | autoskillit skill invocation prefix (Claude: `"/"`; Codex: `"$"`) |
| `session_dir_persistent` | autoskillit session directory lifecycle (Codex retains `codex-sessions/`; Claude uses session JSONL rotation) |
| `cook_startup_observer_capable` | autoskillit guarded startup observation for interactive cook launches |
| `supports_model_invocation_gating` | autoskillit skill materialization policy when a backend cannot enforce model-invocation frontmatter |
| `unnegotiated_tool_result_token_limit` | Backend-owned conservative delivery bound when no protected host evidence is present |
| `protected_recipe_delivery_capable` | Whether a protected host channel can attest a larger recipe-delivery result limit |
| `recipe_delivery_budget` | Version-pinned backend authority for ordinary and protected recipe delivery |
| `hook_trust_policy` | Interactive hook-review policy translated at command construction; Codex uses `REVIEW_EACH_SESSION` |

### 3.3 Category 3: Forward-Declared (7 fields)

Membership in this category is **authoritative from `_FORWARD_DECLARED`** in
`tests/arch/test_capability_consumption.py`. Fields here are declared for
future use and have no current production consumer outside the exemption set.

| Field | Planned consumer |
|---|---|
| `supports_thinking_blocks` | Thinking-block rendering (currently `True` for Claude; `False` for Codex — no production rendering path yet) |
| `required_session_files` | Session directory contract enforcement (Codex: `frozenset({"config.toml"})`; Claude: `frozenset()`) |
| `session_dir_symlinks` | Session directory layout (Codex: `frozenset({"sessions", "archived_sessions"})`; Claude: `frozenset()`) |
| `patch_format` | Write-guard path extraction (Claude: `"unified_diff"`; Codex: `"codex_star_update"`) |
| `min_version` | Version validation in doctor (Codex: `"0.130.0"`; Claude: `""`) |
| `mcp_env_forward_vars` | MCP env forwarding (Codex: `CODEX_MCP_ENV_FORWARD_VARS`; Claude: `frozenset()`) |
| `github_api_callable` | Future network-capability gate for outbound GitHub API writes |


## Section 4: Codex Shim Deviations

The Codex backend (`src/autoskillit/execution/backends/codex.py`) implements
the `CodingAgentBackend` protocol but deviates from Claude Code semantics in
seven categories. Each category is documented below with the discard sites
where Codex silently drops a parameter the protocol accepts.

### 4.1 Approval-Bypass Flags

| Backend | Flag(s) | Source |
|---|---|---|
| Claude Code | `--dangerously-skip-permissions` (non-variadic flag) | `build_cmd` / `build_skill_session_cmd` |
| Codex interactive cook | `--dangerously-bypass-approvals-and-sandbox`; no hook-trust bypass | `HookTrustPolicy.REVIEW_EACH_SESSION` requires review of every generated config |
| Codex automated skill / food truck | `--dangerously-bypass-hook-trust` | Explicit automation policy after AutoSkillit has vetted generated hook source |

Codex splits approval/sandbox bypass from hook trust. Interactive cook never
combines them: every fresh, named-resume, bare-resume, and reload config is
reviewed independently. The hook-trust bypass is limited to automated
builders.

### 4.2 developer_instructions

| Backend | Mechanism | Restriction |
|---|---|---|
| Claude Code | `--append-system-prompt <value>` flag in `build_interactive_cmd` | Incompatible with `--resume` (only applied on `NoResume`) |
| Codex | `-c developer_instructions=<value>` config override (`builder.kv_flag(CodexFlags.CONFIG_OVERRIDE, ...)` at line 931 of `codex.py`) | Same `NoResume` restriction; guarded by `isinstance(resume_spec, NoResume)` at line 930 |

The `-c key=value` form is Codex's config override mechanism; it sets the
`developer_instructions` field in Codex's TOML config rather than passing a
flag.

### 4.3 Positional RESUME_SUBCOMMAND

| Backend | Mechanism |
|---|---|
| Claude Code | `--resume <session_id>` as a flag (named argument) |
| Codex | `resume <session_id>` as a positional subcommand (`CodexFlags.RESUME_SUBCOMMAND = "resume"`, lines 98–107); appears in `build_resume_cmd` (lines 978–979), `build_interactive_cmd`, and `build_skill_session_cmd` |

Codex's resume is a subcommand of the `codex exec` invocation rather than a
flag. The positional form requires the session ID to be present and non-empty
(`build_resume_cmd` raises `ValueError` if `resume_session_id` is empty).

### 4.4 No Channel B

| Backend | Channel B | Consequence |
|---|---|---|
| Claude Code | `channel_b_capable=True` — JSONL side-channel log for session confirmation | Channel B provides authoritative session completion signal; `_retry_fsm.py` treats `CHANNEL_B` as provenance-bypass for `NATURAL_EXIT` |
| Codex | `channel_b_capable=False` — no side-channel | `ChannelConfirmation` dispatch in `_retry_fsm.py` treats Codex sessions as `UNMONITORED`; session confirmation relies on Codex's `turn.completed` / `turn.failed` NDJSON events (`completion_record_types`) and `CodexSessionLocator`'s `thread_id` resolution |

The Channel B gap means Codex cannot use Channel-B-confirmed completion for
the `COMPLETED` termination reason at `_retry_fsm.py:183`; Codex sessions
must rely on `CHANNEL_A` / `UNMONITORED` / `DIR_MISSING` branches.

### 4.5 PTY Is Not Backend-Required

| Backend | PTY | Notes |
|---|---|---|
| Claude Code | `pty_required=True` per `CLAUDE_CODE_CAPABILITIES` (line 219); `ClaudeCodeBackend` delegates PTY allocation to the runner | The constant sets `True` but the backend itself does not allocate the PTY |
| Codex | `pty_required=False` — the backend does not require a PTY | Interactive cook may enable its transparent PTY observer; the process owner controls the group and terminal while the observer owns relay, resize, and raw-mode behavior |

The capability means “not required,” not “prohibited.” Direct and observed
paths share the same finalized `CmdSpec` and process-group cleanup contract.

### 4.6 Inspector RuntimeError

| Backend | `inspector_capable` | `build_inspector_cmd` behavior |
|---|---|---|
| Claude Code | `False` in `CLAUDE_CODE_CAPABILITIES` | Raises `CapabilityNotSupportedError`; unreachable `AssertionError` stub at line 875 is dead code |
| Codex | `False` | Raises `CapabilityNotSupportedError` when `inspector_capable=False` |

Both backends gate via `inspector_capable=False` and raise `CapabilityNotSupportedError`;
no implementation exists in either. An unreachable `AssertionError` at line 875 of
`claude.py` is dead code preserved as a defensive guard for future implementation.

### 4.7 noqa Discard Sites

All discard sites in `codex.py` where a protocol parameter is silently
dropped. Lines 698–701 (`build_skill_session_cmd`) and 821–823
(`build_food_truck_cmd`) use `# noqa: F841` to suppress unused-variable
warnings on local reassignments that document the no-op contract. Line 911
(`build_interactive_cmd`) uses a runtime warning. Lines 1074–1075
(`validate_skill_content`) are a structural discard (entire method body
unconditional).

| Line | Method | Discarded Param | noqa Rule | Reason |
|------|--------|-----------------|-----------|--------|
| 698 | `build_skill_session_cmd` | `plugin_source` | F841 | Codex has no `--plugin-dir` equivalent |
| 699 | `build_skill_session_cmd` | `output_format` | F841 | `--json` is unconditional for Codex |
| 701 | `build_skill_session_cmd` | `exit_after_stop_delay_ms` | F841 | Claude-only feature |
| 821 | `build_food_truck_cmd` | `plugin_source` | F841 | Codex has no `--plugin-dir` |
| 822 | `build_food_truck_cmd` | `output_format` | F841 | `--json` is unconditional |
| 823 | `build_food_truck_cmd` | `exit_after_stop_delay_ms` | F841 | Claude-only feature |
| 911 | `build_interactive_cmd` | `tools` | (warning) | `logger.warning("codex_tools_ignored")` — `tools` arg silently dropped with structured-log warning |
| 1074–1075 | `validate_skill_content` | (entire validation) | (structural) | Returns `[]` unconditionally; no frontmatter requirement |

The F841 sites are deliberately documented as no-ops so the protocol
contract is preserved across both backends: callers can pass
`plugin_source` / `output_format` / `exit_after_stop_delay_ms` and the
Codex backend silently absorbs them. The `tools` arg in
`build_interactive_cmd` (line 911) is the only site that emits a runtime
warning (the others are static no-ops with no observable behavior).

---

## Cross-Reference Summary

| Section | Source of truth | File |
|---|---|---|
| §1 Lifecycle | `CodingAgentBackend` Protocol | `src/autoskillit/core/types/_type_protocols_backend.py` |
| §1 Claude Code methods | `ClaudeCodeBackend` | `src/autoskillit/execution/backends/claude.py` |
| §1 Codex methods | `CodexBackend` | `src/autoskillit/execution/backends/codex.py` |
| §1 Capabilities constant | `CLAUDE_CODE_CAPABILITIES` | `src/autoskillit/core/types/_type_backend.py` lines 217–259 |
| §2 RetryReason enum | `RetryReason` | `src/autoskillit/core/types/_type_enums.py` lines 44–64 |
| §2 Retry routing | `_compute_retry`, `_build_skill_result` overrides | `src/autoskillit/execution/session/_retry_fsm.py`, `src/autoskillit/execution/headless/_headless_result.py` |
| §2 Contract nudge | `_attempt_contract_nudge`, `_merge_token_usage` | `src/autoskillit/execution/headless/_headless_recovery.py` |
| §3 Capabilities | `BackendCapabilities` (47 fields) | `src/autoskillit/core/types/_type_backend.py` |
| §3 Forward-declared | `_FORWARD_DECLARED` | `tests/arch/test_capability_consumption.py` |
| §4 Codex flags | `CodexFlags` | `src/autoskillit/execution/backends/codex.py` lines 98–107 |
| §4 Codex discard sites | `codex.py` F841 / warning sites | `src/autoskillit/execution/backends/codex.py` |
