# Orchestration Levels

AutoSkillit uses four orchestration levels (L0–L3) that describe *who can spawn
whom* at runtime. These are entirely separate from the import-layer IL-N labels
used in module docstrings and `pyproject.toml` import-linter contracts.

## Level Definitions

### L0 — Leaf Subagent

A terminal node (actual leaf) in the execution graph. L0 agents are always
headless, spawned by an L1 session via Claude Code's Agent or Task tool. They
cannot launch sub-agents or headless sessions of their own.

Key properties:

- Always headless (never interactive)
- Spawned via the Agent/Task tool, not `run_skill`
- Cannot call `run_skill`, `run_cmd`, or `run_python`
- Session type: n/a (Claude Agent, not a full session)

### L1 — Session

A Claude Code session (interactive or headless) that can spawn L0 leaf
subagents. When running headless, an L1 is a `run_skill` worker dispatched by
an L2 orchestrator.

Key properties:

- Interactive variant: `autoskillit cook`
- Headless variant: `run_skill` worker
- SessionType: `SKILL` (both interactive and headless)
- Can spawn L0 subagents via Agent/Task tool
- Cannot call `run_skill` (enforced by `skill_orchestration_guard.py` and
  `skill_cmd_guard.py`)

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

- Interactive variant: `autoskillit order` (SessionType `ORCHESTRATOR`)
- Headless variant: food truck (dispatched by L3, SessionType `ORCHESTRATOR`)
- Spawns L1 workers via `run_skill`
- Has full kitchen access (44 kitchen-tagged MCP tools)

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
- Dispatches L2 food trucks via `run_skill`
- Manages campaign state via the sidecar JSONL file

```
L3 (interactive fleet)
└── L2 food truck  (run_skill → headless L2)
    └── L1 worker  (run_skill)
        └── L0 subagent  (Agent/Task tool)
```

## Mapping Table

| Orchestration Level | SessionType enum | CLI command | Headless variant |
|---|---|---|---|
| L0 (leaf) | n/a — Claude Agent | n/a | Always headless |
| L1 (session) | `SKILL` | `autoskillit cook` | `run_skill` worker |
| L2 (orchestrator) | `ORCHESTRATOR` | `autoskillit order` | Food truck |
| L3 (fleet) | `FLEET` | `autoskillit fleet` | None — no L4 exists |

## Key Rules

- **L1 workers cannot call `run_skill`.** The boundary is enforced three ways:
  FastMCP visibility tags, the `skill_orchestration_guard.py` PreToolUse hook,
  and the `_require_orchestrator_or_higher()` runtime guard in
  `tools_execution.py`. All three must independently agree.
- **L0 agents cannot launch anything.** They are terminal nodes — they cannot
  call `run_skill`, cannot invoke the Agent tool to spawn sub-agents, and
  cannot open sub-sessions. (L0 agents are themselves spawned via Agent/Task
  by an L1 — the constraint is on outbound calls only.)
- **L3 has no headless variant.** There is no L4 to dispatch an L3. Fleet
  always runs interactively.
- **Spawning is strictly downward.** An L2 dispatches L1, an L1 spawns L0.
  No level can spawn a peer or a higher level.
- **food trucks are L2, not L1.** A food truck is a headless L2 session
  dispatched by an L3 fleet. It retains full orchestrator capabilities
  (it can call `run_skill` to spawn L1 workers).

## Disambiguation

> Module docstrings and import-linter comments use IL-N (IL-0 through IL-3) for
> the import dependency hierarchy — these are NOT orchestration levels. See the
> import-linter contracts IL-001 through IL-009 in `pyproject.toml`.
