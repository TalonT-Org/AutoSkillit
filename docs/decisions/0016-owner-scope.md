# Dispatch owner scope

A fleet dispatch (L3) owns every process its L2 food truck causes to exist, and it
does not persist a dispatch record or return until all of them are settled. The
owner declares that ownership at spawn time instead of inferring it afterwards
from the L2's process group.

`DefaultHeadlessExecutor.dispatch_food_truck` opens an owner scope with a token
unique to the dispatch run (`dispatch-<dispatch_id>-<nonce>`, so a resume that
reuses the dispatch id is never blocked by an earlier run's seal). The token and
the owner's tether directory travel in the L2 environment. `spawn_owned_process`,
the single funnel for every session-detaching spawn, reads them from the child
environment or, when a caller built a fresh environment without them, from its
own, and stamps the child's tether with the token in the owner's directory. The
shared backend command builder re-injects both variables into L1 launches after
`build_agent_env` scrubs private variables, and Codex forwards them to its MCP
servers through `CODEX_MCP_ENV_FORWARD_VARS`. Every tether written anywhere in
the tree therefore names its owner.

On every exit path the owner seals the scope, so the funnel refuses further
spawns into it, then settles it: repeated, identity-fenced passes kill each live
scoped tether's child and PTY workload trees until a pass finds nothing live, no
kill still to confirm, and no young tether still waiting for its workload
identity. The same settlement runs before each physical L2 attempt through the
executor's pre-spawn admission hook, so a contract nudge never overlaps a prior
attempt's descendants. Settlement is shielded from cancellation and never
raises. When it cannot finish (a survivor of SIGKILL, access denial, or a
settlement error after one retry), the result carries
`infra.cleanup_incomplete=True` and an ERROR log records the evidence. The stale
dispatch reaper seals and settles a dead dispatch's scopes before it writes
state, and only when no fresh heartbeat shows a live owner.

Selection is by registration alone. Reparenting, `setsid`, and a SIGKILLed MCP
server do not hide a descendant, and shared daemons are never selected because
they are never funnel-spawned into a scope. The `OwnedProcessGroup` natural-exit
settlement default is unchanged.

The ownership has to sit in the L3 because the in-server cleanup path cannot run
under Codex. Codex spawns MCP stdio servers with `process_group(0)`,
`env_clear()`, and `kill_on_drop(true)`. Its terminate path sends SIGTERM to the
group and escalates to SIGKILL only after a two-second grace on a detached thread
that dies with the app-server, while dropping the child handle SIGKILLs the
server itself (openai/codex#48524 observes the SIGKILL within milliseconds). The
server's deferred SIGTERM handling therefore never gets to cancel a running
`run_skill`, and the server's own detached children outlive it.

No existing primitive covers this. Structured-concurrency scopes such as anyio
task groups and `asyncio.TaskGroup` own in-process tasks and, at most, a direct
child through its handle, never that child's detached descendants. The
worker-pool pattern of killing a process group is broadcast and pgid-scoped,
the proxy this decision replaces. Durable-execution engines track logical child
workflows, not operating-system processes.

Rejected alternatives:

- Scanning the process table for an environment cookie selects shared daemons
  that inherited it and fails on other users' processes with access denial.
- Periodic ancestry snapshots race short-lived spawns and miss reparented
  children.
- `PR_SET_CHILD_SUBREAPER` only reparents orphans to the subreaper; it neither
  identifies them as the dispatch's nor survives the subreaper's own death.
- Changing the natural-exit process-group default would kill legitimate
  group escapees and still miss `setsid` descendants.
- Relying on in-server cancellation is defeated by the Codex SIGKILL above.

The mechanism is Linux-only, matching the tether registry's identity
primitives. Elsewhere settlement reports itself unsupported, logs one warning
per scope, and changes no behavior.
