# Paper Backend N=3 Exercise: opencode-via-ACP

**Status:** Draft
**Date:** 2026-06-28
**Issue:** [#4052](https://github.com/TalonT-Org/AutoSkillit/issues/4052)
**Candidate:** opencode-via-ACP (paper exercise — no implementation)
**Normative ACP Reference:** [acp-session-contract.md](acp-session-contract.md) (produced by P6-A4-WP1, issue [#4053](https://github.com/TalonT-Org/AutoSkillit/issues/4053))

## Overview

This document classifies every surface of the autoskillit backend abstraction
against the opencode (`github.com/sst/opencode`) coding-agent CLI as the
N=3 paper backend candidate. The exercise walks:

1. **Preamble** — candidate choice, scope, exclusions, and source citations.
2. **Protocol Method Classification** — every method on `CodingAgentBackend`
   and the four sub-protocols (`StreamParser`, `ResultParser`, `EnvPolicy`,
   `SessionLocator`), classified as TRIVIAL / SHIM-REQUIRED / GAP.
3. **BackendCapabilities Field Classification** — every field on
   `BackendCapabilities`, classified using the three-category taxonomy
   (ACP-Mappable / autoskillit-Local Extension / Forward-Declared) inherited
   from `acp-session-contract.md` Section 3.
4. **BackendConventions Classification** — both fields on `BackendConventions`,
   classified and rationalised against opencode's filesystem layout.
5. **Conformance Probe Classification** — the B3a (NDJSON/output schema
   conformance) and B3b (Hook/guard efficacy) probe categories in the
   context of opencode's output format and `--sandbox deny` blocker.
6. **Gap Catalogue — Protocol Change Requests** — one named Protocol change
   request (PCR-NNN) per GAP row across Sections 2–5, with grouping by root
   cause.

The classification is paper-only — no implementation work is planned until the
upstream blockers documented in Section 6 are resolved. The exercise's purpose
is to discover which Protocol surfaces are sufficiently flexible to absorb an
opencode-class backend without extension, and which surfaces require new
Protocol affordances or capability flags.

### Candidate choice

opencode is a Go-based coding-agent CLI distinct from Claude Code (a Node.js
CLI shipping Anthropic's SDK) and Codex (a Rust CLI shipping OpenAI's
Responses API). The choice isolates three independent axes of variation
relevant to the autoskillit backend abstraction:

- **Output format** — opencode emits bulk JSON or terminal-formatted output
  rather than NDJSON, exercising the `StreamParser` / `ResultParser` shim
  pathway rather than the existing fixture-based conformance probe pattern
  in `tests/execution/backends/fixtures/codex_ndjson/`.
- **Session storage** — opencode persists sessions in a SQLite database
  (`~/.local/share/opencode/opencode.db`) keyed by ULID session IDs
  (`ses_<ulid>`), exercising the `SessionLocator` shim pathway rather than
  the per-backend filesystem-locator pattern used by both `ClaudeCodeBackend`
  and `CodexBackend`.
- **Sandbox policy** — `opencode run` hard-codes writes to `deny`
  ([sst/opencode#13851](https://github.com/sst/opencode/issues/13851)),
  exercising the GAP pathway for every `build_*_cmd` method that requires
  headless write capability (`build_cmd`, `build_skill_session_cmd`,
  `build_food_truck_cmd`).

### Source citations

The capability profile in Sections 2–5 derives from three sources:

- **Issue #829** ("Open Code CLI Backend — Deferred") — captured the initial
  capability profile, including the `--sandbox deny` blocker, SQLite
  session storage, ULID session IDs, undocumented exit codes, and the
  exclusion of the community `codex-acp` shim.
- **`acp-session-contract.md`** (P6-A4-WP1, issue #4053) — provides the
  normative ACP reference for lifecycle mapping, recovery ladder, and the
  17 / 6 / 18 capability taxonomy.
- **External research via `sst/opencode` repository** — confirmed the
  output-format gap (bulk JSON or terminal, no NDJSON streaming) and the
  AI SDK multi-provider model (`75+ providers natively via AI SDK`).

> **Note on `external_landscape.md`:** The issue's Technical Step 3
> references `docs/design/external_landscape.md`. That file does not exist
> in the repository; equivalent opencode data was sourced from issue #829
> and web research against the `sst/opencode` repository.

### Explicit exclusions

- **Community `codex-acp` shim** — explicitly excluded from scope. The
  exercise evaluates native opencode only. Adapters that wrap Codex behind
  the ACP surface are not a substitute for opencode-class N=3 coverage and
  would mask the very gaps the exercise is designed to surface.
- **Implementation work** — the document records classifications and
  Protocol change requests only. No `OpencodeBackend` class, no fixture
  files, no conformance probes.
- **Token field derivation** — token fields stored in SQLite are noted as
  "extraction pending" rather than modelled, since the opencode schema is
  undocumented and changes daily with no version guarantee.

---

## Section 1: Preamble

### 1.1 Candidate profile summary

| Dimension | opencode value | Source |
|---|---|---|
| Binary | `opencode` | sst/opencode README |
| Distribution | Single-binary Go release; also `brew install opencode` | sst/opencode README |
| Provider model | AI SDK with 75+ providers (Anthropic, OpenAI, Google, Bedrock, etc.) | sst/opencode README |
| Output format | Bulk JSON or terminal-formatted; no NDJSON streaming | issue #829 |
| Session storage | SQLite (`~/.local/share/opencode/opencode.db`) keyed by ULID (`ses_<ulid>`) | issue #829 |
| Resume / checkpoint | Undocumented; no `--resume` flag surfaced | issue #829 |
| Skill injection | Undocumented | issue #829 |
| Plugin system | None documented | issue #829 |
| Exit codes | Undocumented; daily release cadence with no schema versioning | issue #829 |
| Sandbox policy | `opencode run` hard-codes `--sandbox deny` for writes | sst/opencode#13851 |

### 1.2 Normative reference relationship

The `acp-session-contract.md` document (produced by P6-A4-WP1, issue #4053)
provides:

- **Section 1 (Lifecycle Mapping)** — the canonical 23-row `CodingAgentBackend`
  method enumeration, which this exercise inherits as Section 2's row order.
- **Section 2 (Recovery Ladder)** — the `RetryReason`-to-ACP-rung mapping
  that bounds which rows in Section 6 require new Protocol affordances
  (e.g., a GAP on `build_resume_cmd` implies a new `RetryReason`-to-rung
  pathway for opencode's session model).
- **Section 3 (Capabilities Translation)** — the 17 ACP-Mappable / 6
  Forward-Declared / 18 autoskillit-Local = 41 total taxonomy that this
  exercise's Section 3 adopts and re-applies per-field.

### 1.3 Classification legend

Each row in Sections 2–4 carries one of three classifications:

- **TRIVIAL** — the surface accepts the opencode value as-is (or with a
  constant literal). No adapter code beyond a constructor call.
- **SHIM-REQUIRED** — the surface accepts the opencode value but requires
  adapter code to bridge a format mismatch (NDJSON ↔ bulk JSON, filesystem
  ↔ SQLite, etc.). The Protocol surface is sufficient; only the
  implementation needs a shim.
- **GAP** — the Protocol surface itself is insufficient to express the
  opencode value. A Protocol change request (PCR) is filed in Section 6.

---

## Section 2: Protocol Method Classification

The `CodingAgentBackend` protocol (`src/autoskillit/core/types/_type_protocols_backend.py`,
23 methods) and the four sub-protocols (`StreamParser`, `ResultParser`,
`EnvPolicy`, `SessionLocator`; 7 methods total) yield 30 rows below. The row
order and per-method groupings follow `acp-session-contract.md` Section 1
(Claude Code and Codex columns are omitted; the rightmost column is the
opencode classification).

| # | Protocol | Method | Classification | Rationale |
|---|---|---|---|---|
| 1 | CodingAgentBackend | `name` | TRIVIAL | Returns `"opencode"`. |
| 2 | CodingAgentBackend | `capabilities` | TRIVIAL | Returns a `BackendCapabilities(...)` instance per the rows in Section 3. |
| 3 | CodingAgentBackend | `conventions` | TRIVIAL | Returns a `BackendConventions(...)` instance per the rows in Section 4. |
| 4 | CodingAgentBackend | `build_cmd` | GAP | `opencode run` hard-codes `--sandbox deny` (sst/opencode#13851); headless write execution is structurally blocked. Maps to PCR-001. |
| 5 | CodingAgentBackend | `stream_parser` | SHIM-REQUIRED | No NDJSON streaming — adapter must consume bulk JSON or terminal-formatted output and emit `SessionEvent` values. |
| 6 | CodingAgentBackend | `result_parser` | SHIM-REQUIRED | Aggregates adapted events into `AgentSessionResult`; token fields must be extracted from SQLite since they are not in stdout. |
| 7 | CodingAgentBackend | `env_policy` | TRIVIAL | Standard env construction; opencode consumes provider config via env vars (e.g., `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`). |
| 8 | CodingAgentBackend | `session_locator` | SHIM-REQUIRED | Sessions live in SQLite (`ses_<ulid>`), not filesystem JSONL/NDJSON. The three locator methods require a SQLite-backed adapter. |
| 9 | CodingAgentBackend | `write_tool_names` | SHIM-REQUIRED | opencode's write tool vocabulary is undocumented; needs investigation before the `frozenset` can be populated. Codex returns `frozenset({"apply_patch", "Bash", "run_cmd"})`; opencode likely surfaces a different set. |
| 10 | CodingAgentBackend | `binary_name` | TRIVIAL | Returns `"opencode"`. |
| 11 | CodingAgentBackend | `build_resume_cmd` | GAP | No `--resume <session_id>` flag surfaced; SQLite session IDs are not first-class CLI arguments. Maps to PCR-002. |
| 12 | CodingAgentBackend | `build_skill_session_cmd` | GAP | Blocked by `--sandbox deny`; even if resume were possible, the sandbox constraint forbids skill-session write execution. Maps to PCR-001. |
| 13 | CodingAgentBackend | `build_food_truck_cmd` | GAP | Same sandbox constraint blocks orchestrator-level L2 sessions. Maps to PCR-001 and PCR-003. |
| 14 | CodingAgentBackend | `build_interactive_cmd` | SHIM-REQUIRED | `opencode run` exists for interactive use; sandbox restrictions apply but interactive flows can tolerate a tighter sandbox. |
| 15 | CodingAgentBackend | `validate_session_layout` | TRIVIAL | Returns `[]` (Codex pattern); no session directory to validate when sessions are SQLite-backed. |
| 16 | CodingAgentBackend | `validate_skill_content` | TRIVIAL | Returns `[]` (Codex pattern); no frontmatter requirement when no skill injection is documented. |
| 17 | CodingAgentBackend | `version` | TRIVIAL | `opencode --version` returns the release string; daily cadence but parseable. |
| 18 | CodingAgentBackend | `list_plugins` | TRIVIAL | Returns `[]`; no plugin system documented. |
| 19 | CodingAgentBackend | `ensure_pre_launch` | SHIM-REQUIRED | Pre-launch check that the SQLite database is initialised and the configured provider env vars are present. |
| 20 | CodingAgentBackend | `translate_model` | SHIM-REQUIRED | AI SDK model identifiers differ from autoskillit's canonical aliases (`sonnet` / `opus` / `haiku`). Mapping table needed; precedent is `CODEX_MODEL_ALIASES` in `_type_backend.py:174–176`. |
| 21 | CodingAgentBackend | `model_config_overrides` | TRIVIAL | Returns `()` or a minimal per-model override tuple (analogous to Codex's effort-mapping for `sonnet`/`opus`/`haiku`). |
| 22 | CodingAgentBackend | `build_inspector_cmd` | TRIVIAL | Raises `CapabilityNotSupportedError` (same as both existing backends; `inspector_capable=False`). |
| 23 | CodingAgentBackend | `setup_session_dir` | SHIM-REQUIRED | Bridges SQLite session storage to the filesystem session dir contract; copies the SQLite session row to a session-dir stub so downstream consumers see a directory. |
| 24 | StreamParser | `parse_line` | SHIM-REQUIRED | Adapts non-NDJSON output (bulk JSON or terminal blocks) to `SessionEvent`. Per-line semantics do not apply; the parser operates on whole-output blocks. |
| 25 | ResultParser | `parse_result` | SHIM-REQUIRED | Aggregates adapted `SessionEvent` values into `AgentSessionResult`; identical to existing backends once events are shaped. |
| 26 | ResultParser | `parse_stdout` | SHIM-REQUIRED | Bulk JSON or terminal output parsing; token extraction deferred to SQLite lookup. |
| 27 | EnvPolicy | `build_env` | TRIVIAL | Standard env construction; no denylist required unless opencode leaks through sandbox-bypass env vars. |
| 28 | SessionLocator | `locate_session` | SHIM-REQUIRED | SQLite query by ULID session ID; returns a synthetic `Path` (e.g., the SQLite DB path) for callers that expect filesystem semantics. |
| 29 | SessionLocator | `project_log_dir` | SHIM-REQUIRED | Maps to `~/.local/share/opencode/` (the SQLite directory); per-project logic is opaque since opencode is global-session. |
| 30 | SessionLocator | `session_log_path` | SHIM-REQUIRED | SQLite-backed; no real path exists. Returning the SQLite DB path is the closest analogue. |

### Method-cluster rollup

| Cluster | TRIVIAL | SHIM-REQUIRED | GAP |
|---|---|---|---|
| Identity / introspection (`name`, `capabilities`, `conventions`, `binary_name`, `version`, `list_plugins`, `model_config_overrides`, `build_inspector_cmd`) | 8 | 0 | 0 |
| Command builders (`build_cmd`, `build_skill_session_cmd`, `build_food_truck_cmd`, `build_interactive_cmd`, `build_resume_cmd`) | 0 | 1 | 4 |
| Validation (`validate_session_layout`, `validate_skill_content`, `ensure_pre_launch`) | 2 | 1 | 0 |
| Parsing (`stream_parser`, `result_parser`) | 0 | 2 | 0 |
| Session storage (`session_locator`, `setup_session_dir`, `write_tool_names`) | 0 | 3 | 0 |
| Environment (`env_policy`) | 1 | 0 | 0 |
| Sub-protocols (`StreamParser`, `ResultParser`, `EnvPolicy`, `SessionLocator`) | 1 | 6 | 0 |
| **Total (30 rows)** | **12** | **13** | **5** |

The 5 GAP rows map to 3 distinct root causes (sandbox blocker, resume
absence, orchestrator session absence), consolidated into PCR-001, PCR-002,
and PCR-003 in Section 6.

---

## Section 3: BackendCapabilities Field Classification

The taxonomy below mirrors `acp-session-contract.md` Section 3:

- **ACP-Mappable** — 17 fields with a direct or close ACP analogue.
- **autoskillit-Local Extension** — 18 fields with no ACP analogue but
  required for autoskillit's extended contract.
- **Forward-Declared** — 6 fields with no current production consumer
  outside the exemption set in `_FORWARD_DECLARED`
  (`tests/arch/test_capability_consumption.py:26–63`).

All 41 fields appear in exactly one row below. The "opencode value" column
records what the field would hold for an `OpencodeBackend.capabilities`
instance; the classification column is TRIVIAL / SHIM-REQUIRED / GAP with the
same legend as Section 2.

### 3.1 ACP-Mappable fields (17)

| # | Field | opencode value | Classification | Rationale |
|---|---|---|---|---|
| 1 | `channel_b_capable` | `False` | TRIVIAL | No side-channel JSONL log; SQLite is the only persistence surface. Same shape as Codex. |
| 2 | `pty_required` | `False` | TRIVIAL | opencode's pipe-based stdio; no pseudo-TTY allocation required. |
| 3 | `session_resume_capable` | `False` | GAP | No documented resume mechanism; `build_resume_cmd` is a GAP. Maps to PCR-002. |
| 4 | `skill_injection_capable` | `False` | GAP | No documented `--add-dir` or `--plugin-dir` equivalent. Maps to PCR-004. |
| 5 | `supports_claude_format_stdout` | `False` | TRIVIAL | opencode emits bulk JSON or terminal-formatted output; not Claude format. |
| 6 | `exit_code_is_terminal` | `False` | GAP | Exit codes are undocumented; cannot assert terminal semantics. Maps to PCR-005. |
| 7 | `mcp_config_capable` | `False` (pending) | SHIM-REQUIRED | opencode may have an MCP-style config; research pending. Maps to PCR-006. |
| 8 | `food_truck_capable` | `False` | GAP | Blocked by `--sandbox deny` and absence of orchestrator-level session support. Maps to PCR-001 / PCR-003. |
| 9 | `completion_record_types` | `frozenset()` (pending adapter) | SHIM-REQUIRED | Depends on the bulk-JSON or terminal-output adapter from Section 2 row 5; cannot enumerate record types until the adapter is shaped. |
| 10 | `session_record_types` | `frozenset()` (pending adapter) | SHIM-REQUIRED | Same dependency as row 9. |
| 11 | `triage_capable` | `False` | GAP | No lightweight probe mechanism documented. Maps to PCR-007. |
| 12 | `supports_context_exhaustion_detection` | `False` | GAP | No documented exhaustion signal in stdout or SQLite. Maps to PCR-008. |
| 13 | `process_name` | `"opencode"` | TRIVIAL | Binary stem. |
| 14 | `process_name_aliases` | `frozenset({"opencode"})` | TRIVIAL | Same as `process_name` for a non-shebang binary. |
| 15 | `replay_capable` | `False` | GAP | No recording/replay support documented. Maps to PCR-009. |
| 16 | `record_capable` | `False` | GAP | Same as row 15. Maps to PCR-009. |
| 17 | `inspector_capable` | `False` | TRIVIAL | Same as both existing backends; raises `CapabilityNotSupportedError`. |

### 3.2 autoskillit-Local Extension fields (18)

| # | Field | opencode value | Classification | Rationale |
|---|---|---|---|---|
| 18 | `project_local_skills_capable` | `False` | GAP | No project-local skill discovery mechanism. Maps to PCR-004. |
| 19 | `supports_tool_list_changed` | `True` | TRIVIAL | Default; kitchen reveal timing is unaffected. |
| 20 | `required_skill_fields` | `frozenset()` | TRIVIAL | No frontmatter requirement when no skill injection exists. |
| 21 | `applicable_guards` | `frozenset()` | TRIVIAL | No hook system documented; guards list is empty. |
| 22 | `write_guard_tool_names` | `frozenset()` (pending vocabulary) | SHIM-REQUIRED | Same dependency as Section 2 row 9; vocabulary must be enumerated before the frozenset can be populated. |
| 23 | `env_denylist_prefixes` | `()` | TRIVIAL | No env scrubbing required unless opencode leaks sensitive env vars; defaults to empty. |
| 24 | `version_check_command` | `"opencode --version"` | TRIVIAL | Same pattern as Claude Code / Codex. |
| 25 | `skills_subdir` | `"skills"` | TRIVIAL | See Section 4 caveat — the value is meaningless while `skill_injection_capable=False`. |
| 26 | `hook_config_format` | `""` | TRIVIAL | No hook config; empty string. |
| 27 | `write_detection_strategy` | `""` (pending) | SHIM-REQUIRED | opencode's write evidence format is unknown; strategy cannot be set until the adapter surfaces write events. Maps to PCR-010. |
| 28 | `default_skill_sandbox_mode` | `"workspace-write"` | GAP | Mismatch: opencode hard-codes `--sandbox deny`; the field value is aspirational but cannot be honoured at runtime. Maps to PCR-001. |
| 29 | `anthropic_provider_capable` | `False` | TRIVIAL | Multi-provider via AI SDK; not Anthropic-exclusive. |
| 30 | `plugin_install_capable` | `False` | TRIVIAL | No plugin system. |
| 31 | `supports_context_window_suffix` | `False` | TRIVIAL | AI SDK model identifiers do not carry `[1m]`-style suffixes. |
| 32 | `has_unguarded_filesystem_access` | `True` (pending adapter) | SHIM-REQUIRED | Default `True` matches Codex behaviour; gated prompt supplements apply until the write-detection adapter surfaces the per-tool evidence needed to gate more precisely. |
| 33 | `git_metadata_writable` | `True` | TRIVIAL | opencode's `--sandbox deny` is write-blocked at the user level, not at the kernel level; `.git/worktrees/` remains writable. |
| 34 | `skill_sigil` | `"$"` (defaulted) | SHIM-REQUIRED | No documented invocation prefix; defaulting to Codex's `"$"` until research surfaces an opencode-native convention. Maps to PCR-011. |
| 35 | `session_dir_persistent` | `True` | SHIM-REQUIRED | SQLite-backed sessions are persistent by construction; filesystem bridge must produce a persistent dir (analogous to Codex's `session_dir_persistent=True`). |

### 3.3 Forward-Declared fields (6)

| # | Field | Forward-declared issue | opencode value | Classification | Rationale |
|---|---|---|---|---|---|
| 36 | `supports_thinking_blocks` | #3497 (Claude + Codex pre-existing) | `False` | TRIVIAL | No thinking-block format documented. |
| 37 | `required_session_files` | #3134 (Codex pre-existing) | `frozenset()` | TRIVIAL | No session-dir contract; SQLite-only. |
| 38 | `session_dir_symlinks` | #3134 (Codex pre-existing) | `frozenset()` | TRIVIAL | No symlink targets. |
| 39 | `patch_format` | #3776 (Codex pre-existing) | `""` | SHIM-REQUIRED | Write-detection adapter must surface patch evidence before a format string can be set. Maps to PCR-010. |
| 40 | `min_version` | #3122 (Codex pre-existing) | `""` | TRIVIAL | Version policy deferred; daily cadence makes pinning fragile. |
| 41 | `mcp_env_forward_vars` | #3458 (Codex pre-existing) | `frozenset()` | TRIVIAL | No MCP wiring; empty set. |

### 3.4 Capabilities rollup

| Category | TRIVIAL | SHIM-REQUIRED | GAP |
|---|---|---|---|
| ACP-Mappable (17) | 7 | 2 | 8 |
| autoskillit-Local Extension (18) | 9 | 5 | 4 |
| Forward-Declared (6) | 5 | 1 | 0 |
| **Total (41)** | **21** | **8** | **12** |

The 12 GAP rows consolidate into 9 PCRs (PCR-001 through PCR-009, plus the
sandbox-policy mismatch in row 28 maps to PCR-001); the 8 SHIM-REQUIRED rows
consolidate into PCR-006 / PCR-010 / PCR-011 plus the opencode-vocabulary
adapter family.

---

## Section 4: BackendConventions Classification

Both fields on `BackendConventions` (`src/autoskillit/core/types/_type_backend.py:38–49`)
appear below. The classification legend matches Section 2.

| # | Field | Classification | opencode value | Rationale |
|---|---|---|---|---|
| 1 | `skills_subdir` | TRIVIAL | `Path("skills")` | Conventional default; opencode could use `.opencode/skills/` or `.agents/skills/` if a skill-injection mechanism emerges. Caveat: the value is only meaningful if `skill_injection_capable=True` — which is a GAP for opencode. Setting it now is harmless but inert. |
| 2 | `project_local_skill_search_dirs` | GAP | `()` | opencode has no documented project-local skill discovery. A Protocol extension that surfaces a registration hook (rather than a directory scan) would be required. Maps to PCR-004. |

The dependency between row 1 (`TRIVIAL`) and the `skill_injection_capable`
GAP (Section 3 row 4) is significant: the `skills_subdir` value can be set
without Protocol change, but no consumer can read it until PCR-004 lands.

---

## Section 5: Conformance Probe Classification

The autoskillit test suite carries two probe categories applicable to
backend output formats and write-detection hooks. Each is classified against
opencode's documented behaviour below.

### 5.1 B3a — NDJSON / output schema conformance probes

**Existing pattern.** `tests/execution/backends/fixtures/codex_ndjson/`
contains sealed NDJSON fixtures that the Codex `StreamParser` is validated
against. Each fixture is a JSONL file with named scenarios (e.g.,
`context_exhaustion.jsonl`, `thread_started.jsonl`); the test deserialises
each line, runs it through `parse_line`, and asserts the resulting
`SessionEvent` matches the expected shape. New backends with NDJSON output
add a fixture directory under `tests/execution/backends/fixtures/`.

**opencode classification: GAP.** opencode has no NDJSON streaming — output
is either bulk JSON or terminal-formatted. The fixture-based conformance
probe category requires a new output-format adapter (Section 2 rows 5, 24)
before any probe can be authored. The probe category itself remains valid
as a contract surface; only the authoring pattern changes.

**Required work before B3a probes can land:**

1. Define a sealed output-format schema for opencode (bulk JSON or terminal
   block) — analogous to the Codex NDJSON schema.
2. Author fixture files under `tests/execution/backends/fixtures/opencode_<format>/`
   with the same scenario coverage as the Codex suite (context exhaustion,
   turn completion, turn failure, etc.).
3. Extend `tests/execution/backends/test_stream_parser_conformance.py`
   (or its successor) with an `OpencodeStreamParser` fixture-parameterised
   branch.

Maps to PCR-010 (output-format adapter) and PCR-012 (probe-authoring pattern).

### 5.2 B3b — Hook / guard efficacy probes

**Existing pattern.** `tests/execution/backends/test_hook_deny_efficacy_probe.py`
validates that the backend fires `PreToolUse` / `PostToolUse` hooks for write
operations, with the Codex-specific path labelled B4v. The probe asserts that
the guard fires deterministically for known write tool calls.

**opencode classification: GAP.** opencode's `--sandbox deny` hard-coding
(sst/opencode#13851) means hook firing for write operations cannot be
validated until the upstream blocker is resolved. Two failure modes apply:

- Even with hooks configured, `--sandbox deny` may short-circuit writes
  before the hook fires.
- The hook config file path and format are undocumented; opencode may use a
  different mechanism (e.g., shell-level sandbox) that bypasses the hook
  surface entirely.

Maps to PCR-001 (sandbox-write enablement) and PCR-013 (hook config
discovery).

### 5.3 Probe-category rollup

| Probe category | TRIVIAL | SHIM-REQUIRED | GAP |
|---|---|---|---|
| B3a (NDJSON / output schema) | 0 | 0 | 1 |
| B3b (Hook / guard efficacy) | 0 | 0 | 1 |
| **Total** | **0** | **0** | **2** |

Both probe categories are pure GAPs for opencode, blocking the corresponding
test classes until upstream blockers resolve.

---

## Section 6: Gap Catalogue — Protocol Change Requests

Each GAP row from Sections 2–5 produces a Protocol Change Request (PCR)
entry below. Related rows that share a root cause are consolidated into a
single PCR with multiple affected surfaces. The PCR ID is sequential.

| PCR | Name | Source section | Gap description | Protocol impact | Upstream dependency | Precedent |
|---|---|---|---|---|---|---|
| PCR-001 | sandbox-write-enablement | §2 rows 4, 12, 13; §3 row 8, 28 | `opencode run` hard-codes `--sandbox deny`; every `build_*_cmd` method that requires headless write capability (`build_cmd`, `build_skill_session_cmd`, `build_food_truck_cmd`) is structurally blocked. `food_truck_capable=False` and `default_skill_sandbox_mode="workspace-write"` are aspirational only. | **Protocol change**: a new capability flag (`sandbox_policy_override_capable: bool`) that distinguishes backends whose sandbox can be relaxed via a flag from those whose sandbox is hard-coded. `build_*_cmd` returns must distinguish "capability blocked" from "config rejected". | sst/opencode#13851 | Codex applies `--dangerously-bypass-approvals-and-sandbox` (`codex.py` lines 98–107); Claude Code applies `--dangerously-skip-permissions`. Both have explicit bypass flags; opencode does not. |
| PCR-002 | session-resume-mechanism | §2 row 11; §3 row 3 | No documented `--resume <session_id>` flag; SQLite session IDs are not first-class CLI arguments. `build_resume_cmd` cannot produce a `CmdSpec` that targets an existing session. `session_resume_capable=False`. | **Protocol change**: a new `ResumeSpec` variant (`SqliteBackedResume(session_db_path: Path, session_id: str)`) under `_type_resume.py`, or a generalised `backend-specific-resume` callable on the Protocol surface. | opencode resume feature | Claude Code uses `--resume <session_id>` flag; Codex uses `resume <session_id>` positional subcommand (`codex.py` lines 978–979). |
| PCR-003 | orchestrator-session-support | §2 row 13; §3 row 8 | `build_food_truck_cmd` requires orchestrator-level L2 session support (driven by fleet dispatch); opencode has no equivalent and the sandbox blocker compounds. `food_truck_capable=False`. | **Protocol change**: a new `food_truck_capable` distinction between "no orchestrator support at all" (`False`) and "blocked by sandbox" (new variant or sub-flag). Same proposal as PCR-001 — likely merged into a single new capability. | sst/opencode#13851 | Codex supports food-truck via `build_food_truck_cmd` (lines 821–823) but discards `plugin_source` / `output_format` / `exit_after_stop_delay_ms`. |
| PCR-004 | skill-registration-discovery | §2 rows 9, 12; §3 row 4, 18; §4 row 2 | No documented `--add-dir` / `--plugin-dir` / skill-discovery mechanism. `skill_injection_capable=False`, `project_local_skills_capable=False`, `project_local_skill_search_dirs=()`. | **Protocol change**: a new `skill_registration: Callable[[SkillManifest], None]` field on the Protocol, decoupling registration from CLI flags. | opencode skill-injection feature | Claude Code uses `--add-dir` / `--plugin-dir`; Codex has no equivalent and uses an empty frozenset. |
| PCR-005 | exit-code-semantics | §3 row 6 | Exit codes are undocumented; daily release cadence with no schema versioning. `exit_code_is_terminal=False` is conservative but may yield false positives when opencode exits with a non-zero status on benign conditions. | **Protocol change**: a new `exit_code_is_terminal: Literal["true","false","unknown"]` (string union) to distinguish "verified terminal" from "undocumented". | opencode exit-code documentation | Codex has verified semantics (`exit_code_is_terminal=True`); Claude Code has verified semantics (`False`). |
| PCR-006 | mcp-config-wiring | §3 row 7 | opencode may have an MCP-style config; the absence of documentation makes `mcp_config_capable` ambiguous. SHIM-REQUIRED rather than GAP because the Protocol surface is sufficient; the implementation simply awaits research. | **No Protocol change**. Implementation work only. | opencode MCP documentation | Codex: `mcp_config_capable=True`; Claude Code: `False`. |
| PCR-007 | triage-probe-mechanism | §3 row 11 | No lightweight probe mechanism documented. `triage_capable=False` blocks `_llm_triage.py` from dispatching to opencode. | **Protocol change**: a new `triage_cmd_builder: Callable[[str], CmdSpec]` field that backends can implement to expose lightweight probes without requiring a full skill session. | opencode probe feature | Claude Code uses `claude -p` for triage (`_llm_triage.py`); Codex has no triage path. |
| PCR-008 | context-exhaustion-signal | §3 row 12 | No documented exhaustion signal in stdout or SQLite. `supports_context_exhaustion_detection=False` blocks Codex-style `_headless_result.py` jsonl-context-exhausted handling for opencode. | **Protocol change**: a new `context_exhaustion_signal: Literal["stdout","sqlite","none"]` to distinguish the detection surface. | opencode exhaustion signal | Codex surfaces via `error_code == CODEX_CONTEXT_EXHAUSTION_MARKER` (`_headless_evidence.py` lines 105–108). |
| PCR-009 | recording-replay-support | §3 rows 15, 16 | No recording/replay support documented. `replay_capable=False`, `record_capable=False` blocks api_simulator-based REPLAY_SCENARIO / RECORD_SCENARIO runners. | **No Protocol change**. Implementation work only — api_simulator can wrap opencode once the output adapter (PCR-010) lands. | opencode output-format documentation | Claude Code: `replay_capable=True`, `record_capable=True`. Codex: not yet wired. |
| PCR-010 | output-format-adapter | §2 rows 5, 24, 26; §3 rows 9, 10, 27, 39; §5 B3a | opencode emits bulk JSON or terminal-formatted output, not NDJSON. The `StreamParser` / `ResultParser` pair require an adapter; `completion_record_types`, `session_record_types`, `write_detection_strategy`, `patch_format` all depend on the adapter's surface. | **Protocol change**: a new `output_format: Literal["ndjson","bulk_json","terminal"]` field on the Protocol so consumers can dispatch on the actual format rather than assuming NDJSON. | opencode output-format documentation | Both existing backends emit NDJSON; this is the first non-NDJSON candidate. |
| PCR-011 | skill-sigil-convention | §3 row 34 | No documented invocation prefix. Defaulting to Codex's `"$"` until research surfaces an opencode-native convention. | **No Protocol change**. Implementation choice only. | opencode invocation convention | Claude Code uses `"/"`; Codex uses `"$"`. |
| PCR-012 | conformance-probe-authoring | §5 B3a | The fixture-based conformance probe pattern (`tests/execution/backends/fixtures/codex_ndjson/`) assumes NDJSON; opencode requires a new authoring pattern. | **No Protocol change**. Test infrastructure extension only. | PCR-010 | Codex NDJSON fixtures. |
| PCR-013 | hook-config-discovery | §5 B3b | Hook config file path and format are undocumented; opencode may use shell-level sandbox or another mechanism that bypasses the autoskillit hook surface. | **Protocol change**: a new `hook_config_paths: tuple[Path, ...]` field on `BackendCapabilities` that backends populate to declare the files the probe should write and observe. | opencode hook documentation | Codex: `hook_config_format="toml_nested"` (forward-declared); Claude Code: `hook_config_format=""`. |

### 6.1 PCR rollup

| Classification | Count |
|---|---|
| Protocol change required | 8 (PCR-001, 002, 003, 004, 005, 007, 008, 010, 013) |
| Implementation-only | 3 (PCR-006, 009, 011) |
| Test-infrastructure extension only | 1 (PCR-012) |
| **Total** | **13 PCRs** |

Note: PCR-001 and PCR-003 share the upstream sandbox blocker; a single
sandbox-write-enablement change request in `sst/opencode` would unblock
both. PCR-005 and PCR-008 are documentation-driven and may resolve without
Protocol change once `sst/opencode` publishes exit-code and exhaustion
semantics.

### 6.2 Cross-reference summary

| Section | Source of truth | File |
|---|---|---|
| §2 method enumeration | `CodingAgentBackend` Protocol + 4 sub-protocols | `src/autoskillit/core/types/_type_protocols_backend.py` |
| §3 capability taxonomy | `BackendCapabilities` (41 fields) + `_FORWARD_DECLARED` | `src/autoskillit/core/types/_type_backend.py`, `tests/arch/test_capability_consumption.py` |
| §3 capability categories | ACP-Mappable / autoskillit-Local / Forward-Declared counts | `docs/design/acp-session-contract.md` Section 3 |
| §4 conventions | `BackendConventions` (2 fields) | `src/autoskillit/core/types/_type_backend.py:38–49` |
| §5 B3a pattern | Codex NDJSON fixtures | `tests/execution/backends/fixtures/codex_ndjson/` |
| §5 B4v pattern | Codex hook-efficacy probe | `tests/execution/backends/test_hook_deny_efficacy_probe.py` |
| §6 PCR-001 / 003 blocker | `--sandbox deny` hard-coding | sst/opencode#13851 |
| §6 PCR-002 / 005 / 008 documentation | Resume, exit codes, exhaustion signals | sst/opencode repository |