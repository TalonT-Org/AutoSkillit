# guards/

PreToolUse guard scripts — standalone Python processes enforcing tool-call policies.

The package initializer remains import-free.

## Guard Capabilities

- **Session and tool access:** user questions require an open kitchen; headless sessions
  cannot open the kitchen, and headless skill sessions cannot request background Bash or
  Agent execution. L1 skill sessions cannot call `run_skill`, `run_cmd`, or `run_python`.
  Skill commands must use the canonical slash-command path form.
- **Git and pull requests:** operations against protected branches are denied. With the
  kitchen open, `gh pr create` through `run_cmd` is blocked, and PR bodies must carry the
  required `Closes #N` reference. In headless skill sessions, git operations block
  `commit --amend`, `push --force`, `reset --hard`, `clean -f`, and `checkout .`.
  Raw GitHub review publication is fail-closed across Bash and `run_cmd`: the guard denies
  review/comment writes, multiple or dynamically unresolved mutations, ambiguous CWD or
  input-file authority, and permits only proven single non-review operations such as a
  fully literal `resolveReviewThread` mutation.
- **Fleet lifecycle:** headless fleet dispatch is denied to prevent L3-to-L3 recursion.
  A fresh dispatch cannot claim an issue already marked `in-progress`; it must resume via
  `resume_session_id`. Reset requires an earlier resume attempt unless `force=true`, with
  name-to-UUID state resolution and a REFUSED-dispatch exemption. Resume ownership denies
  unowned and L3 sessions, and review-loop actions remain gated on `check_review_loop`.
- **Files and commands:** generated writes to `hooks.json`, `settings.json`, and recipe
  `contracts/` are blocked. Headless command tools cannot read recipe/skill/agent files
  directly. Grep rejects BRE `\|` alternation and supplies the POSIX ERE
  form. Artifact downloads require `--dir`; editable system-Python installs, direct
  `pytest` or `python -m pytest`, and destructive git commands are denied.
- **Pipeline and planner policy:** unmet pipeline dependencies produce an advisory while
  the server remains the primary enforcer. Planner writes require canonical result names,
  planner sessions cannot discover issues or PRs through GitHub listings, quota limits
  block skill runs but fail open when their cache is missing, and ingredient locks are
  enforced as a supplement to the server gate.
- **Skill loading:** non-Anthropic headless skill sessions must load the skill before
  native tools are used. The load gate exempts Codex, subagents carrying `agent_id`, and
  sessions whose `AUTOSKILLIT_APPLICABLE_GUARDS` omits the guard stem.
- **Advisories and cleanup:** recipe YAML writes and a disconnected MCP server produce
  non-blocking advisories. Clone removal is denied when the branch has unpushed commits.
- **Write scope:** write-scoped sessions may use configured write tools only within their
  allowed prefix. Codex is exempt because its workspace-write sandbox provides this
  boundary; other backends use `AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES`.

## Architecture Notes

Each guard is a standalone Python script executed as a subprocess (not imported as a module). Protocol: read PreToolUse JSON from stdin, write decision JSON to stdout, exit 0. Most are stdlib-only for fast startup.

### Fail-Mode Contract

All guards fail-**open** for malformed/unparseable input (JSON decode failure = exit 0 = approve).
This prevents a broken hook from blocking the entire tool chain.

Five guards additionally fail-**closed** for valid input with unrecognized values, as a
defense-in-depth measure against privilege escalation:

| Guard | Fail-closed condition | Rationale |
|-------|----------------------|-----------|
| `skill_command_guard.py` | Unexpected runtime error (not JSON parse) | Unknown failure mode = deny rather than risk executing an unvalidated command |
| `open_kitchen_guard.py` | Unrecognized `AUTOSKILLIT_SESSION_TYPE` | Unknown session type should not gain kitchen access |
| `skill_orchestration_guard.py` | Unrecognized `AUTOSKILLIT_SESSION_TYPE` | Unknown session type should not call orchestration tools (`run_skill`, `run_cmd`, `run_python`) |
| `background_exec_guard.py` | Unrecognized `AUTOSKILLIT_SESSION_TYPE` | Unknown session type should not bypass `run_in_background` prohibition |
| `github_mutation_guard.py` | Ambiguous or unresolved GitHub mutation command | Unknown mutation scope must not bypass the structured review publisher |

**Design principle:** Garbage-in (malformed hook input) = fail-open. Unknown-tier (valid input, unrecognized value) = fail-closed.

## Hook Payload Fields for Guard Development

### `agent_id` — Subagent Detection

Claude Code includes `agent_id` in the hook JSON payload (stdin) when the hook fires
inside a subagent (L0). The field is absent in top-level sessions. This is the standard
mechanism for detecting subagent context in hook scripts.

- `agent_id` — present ONLY in subagent context; absent at top-level
- `agent_type` — also present in subagent context (e.g., `"Explore"`, `"general-purpose"`)
- Subagents cannot spawn nested subagents, so the check is binary: top-level vs. one-level-deep
- Check: `if data.get("agent_id"): sys.exit(0)` for unconditional subagent exemption

Used by: `skill_load_guard.py`, `skill_load_post_hook.py`
