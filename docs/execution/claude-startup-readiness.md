# Claude startup readiness

`autoskillit order` has two distinct startup boundaries:

1. Claude must finish its bounded MCP connection/list snapshot before the first
   model dispatch can rely on `open_kitchen`.
2. Once a `CallToolResult` exists, recovery belongs to the kitchen-opening result
   protocol. It is no longer a client-addressability failure.

Server initialization alone does not prove the first boundary. The readiness
contract is based on client-observed interactive traces.

## Supported launch policy

Claude interactive launches seal these values after caller environment overlays:

| Setting | Value | Meaning |
|---|---:|---|
| `MCP_CONNECTION_NONBLOCKING` | `0` | Ask Claude to wait for the connection batch |
| `MCP_CONNECT_TIMEOUT_MS` | `30000` | Bound the connection batch and tool-list snapshot |
| minimum Claude Code version | `2.1.142` | First supported nonblocking-startup contract |
| fresh prompt attempts | `3` | Defense-in-depth dispatch cap after a pre-dispatch failure |

`MCP_CONNECT_TIMEOUT_MS` is the connection batch/list-snapshot deadline. It is
not `MCP_TIMEOUT`, the distinct per-server connection timeout. Claude may let a
pending server continue in the background after the snapshot deadline, so the
configuration does not claim that an over-budget server is addressable.

Interactive launch is a two-stage transaction. A provisional binding fingerprints
the selected executable and directs only the `--version` probe. After the probe
returns capability attestation as data, the launcher builds the authoritative
session environment and creates a final content-sealed binding. The provisional
and final executable identities must be equal, the final command rebuild must
match the sealed environment, and file continuity is checked again immediately
before spawn.

The session environment is sealed exactly once, after all capability-dependent
inputs are known; only that sealed environment reaches the session. The probe
inherits OS/runtime variables and executable selectors (`PATH` and, for Claude,
`CLAUDE_CODE_EXECPATH`), but AutoSkillit-added session state, lineage, provider
credentials, and other final-session extras are withheld. The former readiness
checks for MCP policy keys on the probe binding are intentionally absent: those
keys belong to the final builder output and are set there unconditionally, so a
probe-binding check would be unreachable under this transaction.

Unprobed resume, skill-session, and food-truck builders conservatively attest no
annotation support. They may claim support only when a caller explicitly threads
attestation from an exact successful probe. The identity checks are portable
fail-closed drift detection, not atomic executable binding: pathname replacement
after the final check and writable same-file mutation remain residual risks.

## Fresh and resumed sessions

Final `NoResume` launches receive the rendered startup-recovery contract. Every
failure before a `CallToolResult` causes the next bounded `open_kitchen` attempt
without an explanation, troubleshooting text, a free-text question, or
`AskUserQuestion`. Exhaustion emits one fixed terminal message.

Final `NamedResume` and `BareResume` launches do not receive an appended system
prompt because Claude does not support that combination with `--resume`.
Requested resume flows that resolve to final `NoResume` are fresh launches and
do receive the contract. Resumed launches are accepted only when the live client
trace demonstrates a pre-turn barrier or a bounded fail-closed outcome; prompt
text is not credited when it cannot be delivered.

Receipt of any `CallToolResult` ends pre-dispatch recovery. `isError:true` is a
received tool result, while `isError:false` contains the structured application
result, including `success:false`.

## Conformance matrix and artifacts

The weekly `conformance-probes.yml` workflow runs these pinned rows:

| Platform | Claude Code |
|---|---|
| Ubuntu 22.04 | 2.1.142 and 2.1.197 |
| macOS 14 | 2.1.142 and 2.1.197 |

Each row installs the named version, exports its canonical path as
`CLAUDE_CODE_EXECPATH`, and runs the exact restricted interactive surface with
`--tools AskUserQuestion`. The probe covers immediate readiness, a delay inside
the connection budget, and a delay beyond it. Bounded JSONL traces are written
under `.autoskillit/temp/claude-startup-readiness/` and uploaded on failure.

A matrix row is supported only after its workflow run passes. Tracked source
does not turn a missing or failed live trace into evidence. Run the same probe
locally with an explicitly injected isolated `ANTHROPIC_API_KEY` or
`CLAUDE_CODE_OAUTH_TOKEN`:

```bash
task test-smoke-claude-startup
```

The current probe retains at most 256 KiB of terminal output and records its
byte count and digest. A future server-level `alwaysLoad` change must add
client-observed pre-open and post-open tool-schema byte measurements before it
can be accepted.

## `alwaysLoad` decision

`open_kitchen` retains its existing per-tool
`_meta["anthropic/alwaysLoad"]=true`. The source `.mcp.json` does not set
server-level `alwaysLoad`: dynamic FastMCP visibility means that loading the
whole server could increase initial schema cost, and that cost has not been
accepted by the conformance matrix. Any later change must compare against the
per-tool baseline and retain source, projected, and installed configuration
equality.

## Rejected substitutes

The launch contract does not use:

- a fixed sleep before Claude starts;
- the server readiness sentinel as proof of client tool addressability;
- an unbounded model retry loop or a prompt-owned wall clock;
- a headless preflight followed by an unproven resume handoff; or
- removal of headless-session safeguards.

Primary client contracts:

- [Claude Code environment variables](https://code.claude.com/docs/en/env-vars)
- [Claude Code MCP deferral and `alwaysLoad`](https://code.claude.com/docs/en/mcp#exempt-a-server-from-deferral)
- [FastMCP tool visibility](https://gofastmcp.com/servers/visibility)
