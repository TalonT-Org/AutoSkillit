# Orchestration Levels

AutoSkillit uses four orchestration levels (L0–L3) that describe *who can spawn
whom* at runtime. These are entirely separate from the import-layer IL-N labels
used in module docstrings and `pyproject.toml` import-linter contracts.

## Level Definitions

### L0 — Leaf Subagent

A terminal node (actual leaf) in the execution graph. L0 agents are always headless and cannot
launch sub-agents or headless sessions of their own. Ordinary leaves use the backend-native
Agent/Task mechanism; behavioral evidence readers use the dedicated delegation boundary below.

Key properties:

- Always headless (never interactive)
- Spawned through a backend-native leaf mechanism or dedicated evidence delegation, not `run_skill`
- Cannot call `run_skill`, `run_cmd`, or `run_python`
- Session type: n/a (Claude Agent, not a full session)

The specialized Codex explorers are L0 leaves as well. An L1 exploration parent may invoke
`semantic-code-navigator` or `repository-impact-profiler`; each is terminal, read-only, and
returns evidence for the parent to synthesize. They cannot delegate, mutate the repository, or
replace the deterministic repository collectors. See [Explorer agents](execution/explorer-agents.md).

Behavioral evidence readers are also terminal L0 leaves, but use a distinct launch boundary. A
writable L1 Codex skill session calls `delegate_evidence_reader`, and AutoSkillit synchronously
joins a separate sterile top-level process. The reader sees only its authenticated one-artifact
brokers, not the repository or ordinary kitchen/headless surfaces. This preserves the writable
parent while keeping the reader's evidence contract read-only and non-delegating.

This is the single narrow exception to ordinary backend-native L1-to-L0 routing: it exists because
Codex cannot narrow a writable parent's sandbox for a native child. The implementation is one
concrete delegation boundary, not a second general routing framework; another abstraction is not
warranted until a second production caller needs it. Issue #4563 separately owns the audit-reader
role, Git-handler and ref-validation surfaces, and ready-wave scheduling. Those concerns are not
part of this evidence-reader pilot.

### L1 — Session

A Claude Code session (interactive or headless) that can spawn L0 leaf
subagents. When running headless, an L1 is a `run_skill` worker dispatched by
an L2 orchestrator.

Key properties:

- Interactive variant: `autoskillit cook`
- Headless variant: `run_skill` worker
- SessionType: `SKILL` (headless variant); interactive cook carries CLI label `"cook"` (not a SessionType enum member)
- Can spawn L0 subagents via Agent/Task tool
- Headless variant cannot call `run_skill` *in headless mode* (enforced by
  `skill_orchestration_guard.py` and `skill_cmd_guard.py`); interactive L1 sessions
  bypass all tier guards via the ``AUTOSKILLIT_HEADLESS`` short-circuit

```
L1 (interactive cook)
└── L0 subagent  (Agent/Task tool)
    └── [terminal — spawns nothing]

L1 (headless run_skill worker)
└── L0 subagent  (Agent/Task tool)
    └── [terminal — spawns nothing]
```

### L2 — Orchestrator

Orchestrates L1 headless sessions by dispatching them via `run_skill`. The L2
reads the recipe, calls MCP tools, and routes verdicts. It never reads or writes
code itself.

Key properties:

- Interactive variant: `autoskillit order` (CLI label `"order"`; headless equivalent carries SessionType `ORCHESTRATOR`)
- Headless variant: food truck (dispatched by L3, SessionType `ORCHESTRATOR`)
- Owns `run_skill` exactly and spawns L1 workers through it
- Has full kitchen access (51 kitchen-tagged MCP tools)

```
L2 (interactive order)
└── L1 worker  (run_skill)
    └── L0 subagent  (Agent/Task tool)

L2 (headless food truck, dispatched by L3)
└── L1 worker  (run_skill)
    └── L0 subagent  (Agent/Task tool)
```

### L3 — Fleet Dispatcher

Manages a fleet of L2 food trucks, dispatching them to process batches of
issues or repositories. There is no L4, so L3 has no headless variant.

Key properties:

- Interactive only: `autoskillit fleet` (SessionType `FLEET`)
- No headless variant (nothing above L3 to dispatch it)
- Dispatches L2 food trucks via `dispatch_food_truck`
- Cannot call `run_skill`; doing so would skip the L2 boundary
- Manages campaign state via the sidecar JSONL file

```
L3 (interactive fleet)
└── L2 food truck  (dispatch_food_truck)
    └── L1 worker  (run_skill)
        └── L0 exploration leaf  (backend-native agent dispatch)
```

## Mapping Table

| Orchestration Level | SessionType enum (headless) | CLI label (interactive) | CLI command | Headless variant |
|---|---|---|---|---|
| L0 (leaf) | n/a — backend-native agent | n/a | n/a | Always headless |
| L1 (session) | `SKILL` | `"cook"` | `autoskillit cook` | `run_skill` worker |
| L2 (orchestrator) | `ORCHESTRATOR` | `"order"` | `autoskillit order` | Food truck |
| L3 (fleet) | `FLEET` | `"fleet"` | `autoskillit fleet` | None — no L4 exists |

> **Note:** `SessionType` enum values (`SKILL`, `ORCHESTRATOR`, `FLEET`) apply to headless
> sessions only and are read from the `AUTOSKILLIT_SESSION_TYPE` environment variable.
> Interactive sessions carry CLI display labels (`"cook"`, `"order"`, `"fleet"`) that are
> not `SessionType` enum members and bypass tier enforcement via the
> `AUTOSKILLIT_HEADLESS` short-circuit in tier guards.

## Key Rules

- **`run_skill` is exact-L2.** Headless L1 and L3 sessions are denied. The boundary is
  enforced by visibility, `skill_orchestration_guard.py`, and the
  `_require_orchestrator_exact()` runtime guard in `tools_execution.py`.
- **`run_cmd` and `run_python` remain L2-or-higher.** L2 and L3 may call them; headless
  L1 may not. Interactive sessions retain the existing headless-guard bypass.
- **L0 agents cannot launch anything.** They are terminal nodes — they cannot
  call `run_skill`, spawn sub-agents, or open sub-sessions. An L1 launches an L0
  through the backend-bound native convention: Claude uses `Agent`, while Codex
  uses `spawn_agent`. The constraint is on outbound calls from the leaf.
- **L3 has no headless variant.** There is no L4 to dispatch an L3. Fleet
  always runs interactively.
- **Spawning is strictly downward.** L3 dispatches L2 with `dispatch_food_truck`, L2
  dispatches L1 with `run_skill`, and L1 dispatches L0 through its bound backend convention.
  No level can spawn a peer or a higher level.
- **food trucks are L2, not L1.** A food truck is a headless L2 session
  dispatched by an L3 fleet. It retains full orchestrator capabilities
  (it can call `run_skill` to spawn L1 workers).

## Disambiguation

> Module docstrings and import-linter comments use IL-N (IL-0 through IL-3) for
> the import dependency hierarchy — these are NOT orchestration levels. See the
> import-linter contracts IL-001 through IL-009 in `pyproject.toml`.
