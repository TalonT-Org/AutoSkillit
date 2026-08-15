# Specialized explorer native capability conformance gate

## Decision

AutoSkillit uses two terminal exploration leaves:

- `semantic-code-navigator` plans structural and semantic repository queries.
- `repository-impact-profiler` plans registry, configuration, artifact, test, and consumer queries.

The L1 parent owns routing, adaptive cross-leaf handoff, and final synthesis. Leaves do not
spawn peers or one another. Deterministic code owns repository identity, snapshot capture,
collector precedence, relationship semantics, merge, pagination, and completeness. Phase A
registers neither production role; it injects one standalone `semantic-code-navigator`
definition only into the live probe.

## Native Codex policy

The Codex projection is independently validated against native model and reasoning-effort
registries. The explorer policy is `gpt-5.6-luna`, `max`, and `read-only`; Claude model aliases
remain unchanged. A canonical, domain-separated `AgentDef` digest is embedded in generated
developer instructions and must be present in the child rollout's Codex-owned
`session_meta.base_instructions`.

At probe startup, the complete `codex debug models --bundled` catalog is validated and then
projected only for Luna: bundled `tool_mode=code_mode_only` and
`apply_patch_tool_type=freeform` become `tool_mode=direct` and
`apply_patch_tool_type=null`. The projected complete catalog is supplied to the isolated session;
the generated role also writes `[agents]` with `enabled=false`.

Parent sandbox policy is persisted separately from the requested child policy. Codex CLI 0.146
applies a custom role and then reapplies the live parent's permission profile before spawning the
child. Consequently a role cannot narrow a workspace-write parent. The Phase A probe therefore
requires a read-only parent, retains the highest-precedence `--sandbox read-only` CLI flag, and
rejects its injected Luna/max/read-only definition before generated-home mutation when the parent
is not read-only. This proves effective read-only inheritance; it does not claim child-specific
narrowing.

## Runtime evidence authority

The release gate accepts identity only from linked Codex rollout records:

- The parent `turn_context` owns its effective sandbox, approval policy, and permission profile.
- `turn_context` owns effective model, effort, sandbox, approval policy, and permission profile.
- `session_meta` owns the child ID, parent linkage, role, agent path, CLI version, and base
  instructions.
- `base_instructions.text` owns delivery of the canonical definition digest.

Generated TOML, requested settings, a parent response, or an ambiguous/malformed record cannot
satisfy the gate. The probe also validates `codex debug models --bundled`, requires Luna to
advertise `max`, and binds a canonical model-catalog digest into the attestation.

Codex 0.146 does not record a complete offered-tool inventory. Accordingly, the gate binds the
CLI version, generated configuration, and catalog projection, then verifies the exact observed
MCP calls: `bounded_literal_search`, `parse_python_ast`, `optional_capability_status`, and
`deny_operations`. Their server configuration requires explicit `approve`; this is evidence about
the observed calls, not a claim about Codex's total tool inventory.

Behavioral evidence readers use the same deliberately bounded observation standard, but not the
Explorer role or authority. Their separate top-level Codex process binds an exact generated
configuration, projected catalog, installed CLI/authentication shape, structured-output canary,
and the observed successful calls to the two authenticated one-artifact brokers. Unknown,
failed, or forbidden observed calls reject the result. This proves the behavior exercised by the
reader; it does not claim that Codex exposes a complete inventory of every built-in tool it may
have offered.

## Security matrix

The direct native-shell spike is not an acceptable boundary. Codex 0.146 permission profiles
govern filesystem and network access but do not govern executable or exact argv selection. Its
exec-policy rules match prefixes, accept trailing arguments, and allow unmatched commands when
approval is `never`. Read-only and no-exec mounts also cannot distinguish a non-executing AST read
from an interpreter importing the same source bytes.

The Phase A child therefore starts from a sterile directory without the seeded repository as its
working directory or an `--add-dir`. Its generated role disables the native shell and unified-exec
variants, JS/code runtimes, nested agents, apps, browser/computer use, and web search. The ordinary
AutoSkillit MCP server is disabled for this probe. Exactly one test-only server is enabled with an
observed four-call contract: fixed-file bounded literal search, fixed-file stdlib AST parsing,
optional-capability status, and closed-set denial recording. It exposes no arbitrary path,
command, argv, resource, template, subprocess, import, LSP process, or network input. Direct
descriptor-relative reads reject symlinks and enforce byte, input, output, and match bounds.

The parent prompt still gives the child the hostile repository path and requires adversarial use
of any shell or nested-agent tool that unexpectedly remains visible. Any such regression can
execute the target canary or create an extra rollout and fails the gate. The broker separately
records successful bounded collectors and every requested mutation, Git, target-execution,
credential, repository-policy, and network operation as denied without attempting it. Post-run
filesystem, Git, rollout-tree, network-listener, broker-audit, credential, policy, and target
canaries are independently checked. The attestation binds a digest of the disabled feature set,
exact MCP allowlist, and absent direct repository mount; evidence from the older direct-shell
contract cannot satisfy the gate.

The Phase A broker is a capability harness, not the production repository broker. Phase B still
owns trusted repository context and snapshot identity, complete collector implementations,
production MCP registration and visibility, result contracts, and production role registration.
Optional Tree-sitter and headless-LSP remain non-prerequisites; the Phase A harness reports them
unsupported rather than launching target-aware analyzers.

### Production provisioning models (#4488/#4489/#4492)

Two backend-specific provisioning models are now first-class:

- **Codex (terminal per-child):** Each explorer child is a separate process with its own
  server boot. The `shared-explorer-session` principal is confined to a dedicated read-only
  child process via per-child env binding (`_codex/explorer_projection.py`). Authority is
  verified by `_explorer_auto_gate_boot` at lifespan time.

- **Claude (session-scoped in-process):** Claude subagents share the parent process — the
  per-child terminal model structurally cannot apply. The current
  [PreToolUse event](https://code.claude.com/docs/en/hooks) supplies the native
  `session_id`; parent and subagent calls use distinct one-shot records but the same
  session lease key. The hook copies `tool_input`, overwrites only an opaque token, and
  never persists or authorizes with `agent_id`. The server atomically consumes the
  short-lived, exact-tool-bound record before resolving the existing HMAC lease.

  The token's entropy, atomic claim, exact-tool binding, and existing lease are the
  authority boundary. Record mtime is only a conservative cleanup signal. `ToolContext`,
  FastMCP client-session IDs, and newest kitchen markers are prohibited identity sources.
  This assumes the supported Linux/macOS native local filesystem and excludes hostile
  same-UID processes; no network-filesystem atomicity or durability claim is made.

Tag visibility (`mcp.enable(tags={"exploration"})`) is UX defense-in-depth. The per-call
HMAC capability lease is the authorization boundary. Every broker call re-verifies the
lease regardless of tag visibility state.

Broker handlers remain terminal-first: successful Codex/headless launch authority never
requires or consumes a hook token. The [2026-07-28 MCP specification release](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
and SEP-2567 reinforce avoiding protocol-session identity; no documented FastMCP bridge
currently correlates a Claude parent with its native subagent.

Both explorer role bodies carry a mandatory tool-surface self-check preamble: if the
effective surface is anything other than exactly the three broker tools, the role emits a
structured CONTRACT VIOLATION report and stops without performing work.

## Attestation and stop condition

`task test-smoke-codex-explorer-gate` is credentialed, non-skippable when selected, bypasses the
general conformance cache, and publishes one versioned attestation below
`.autoskillit/temp/conformance/`. CI runs it before restoring the ordinary probe cache and uploads
the ephemeral attestation.

The attestation is accepted only when it is a cache miss and exactly matches the CLI version,
model-catalog digest, probe-policy/contract version, effective read-only parent and child policy,
never approval, restricted network, exact parent/child identities, role/agent path, canonical
definition digest, tool-surface digest, required-boundary results, and optional capability
statuses. It also pins the `gpt-5.6-sol` parent model, validates a fresh timestamp and the exact
versioned attestation artifact schema, and runs with an allowlisted isolated `HOME`, `CODEX_HOME`,
and XDG environment. A SHA-256 sidecar binds the exact published payload; the separate readiness
step verifies it plus the installed CLI and current projected catalog, and requires enforced
target-execution and credential isolation. Any negative observation is still published for
diagnosis and then fails the gate. Production roles, the deterministic production broker, portable
dispatch, telemetry expansion, and skill/lens adoption must not be created until this gate passes.

## Performance and invalidation

Phase A does not mandate a persistent index. The capability-spike baseline below was measured on
2026-08-01 over the current 563-file `src/**/*.py` tree. Each row used six fresh processes; “cold”
is the first process and “warm” is the median of the next five. The OS page cache was not dropped,
so these numbers distinguish process/collector startup from repeated cache-warm execution rather
than claiming a physical-disk cold start.

| Collector operation | Cold process | Warm median |
|---|---:|---:|
| bounded ripgrep (`--no-config --no-follow`) | 8.1 ms | 8.3 ms |
| stdlib AST parse of all Python source | 500.4 ms | 511.5 ms |
| Tree-sitter parse of all Python source | 427.2 ms | 446.8 ms |
| Pyright headless LSP initialize/shutdown | 200.9 ms | 215.7 ms |
| repository extractors (`ls-files --stage` plus porcelain-v2 status) | 16.5 ms | 12.1 ms |

The measurement environment was Python 3.11.14, ripgrep 15.2.0, Tree-sitter 0.25.2 with the
Python grammar 0.25.0, Pyright 1.1.409, hyperfine 1.19.0, and Codex CLI 0.146.0. Raw timing samples
remain session-local under `.autoskillit/temp/retry-worktree/` and are not runtime authority.

The live gate records the exact CLI/catalog identity that invalidates its result. Phase B must
remeasure these operations through the production collectors against immutable snapshots. Any
CLI/catalog, collector manifest, profile, query, repository snapshot, grammar/LSP version, or agent
definition change invalidates corresponding cached evidence; no timing observation may weaken the
security or completeness gate.
