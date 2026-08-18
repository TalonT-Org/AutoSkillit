# FAQ

### What is AutoSkillit?

A Claude Code plugin that runs YAML recipes through a two-tier orchestrator.
Bundled recipes turn GitHub issues into merged PRs by chaining plan,
dry-walkthrough, worktree, test, and PR-review skills. See
[getting-started.md](getting-started.md).

### How many MCP tools does it expose?

52. Fifteen are free-range: four always-visible (`open_kitchen`, `close_kitchen`,
`disable_quota_guard`, `reload_session`) plus eleven fleet tools revealed only in
fleet sessions via the `fleet`/`fleet-dispatch` tags. The remaining 37 are
kitchen-tagged (gated behind `open_kitchen`). One kitchen tool, `test_check`,
also carries the `headless` tag and is revealed only inside headless sessions.
See [execution/tool-access.md](execution/tool-access.md).

### How many bundled skills are there?

125: 3 in `src/autoskillit/skills/` (Tier 1) and 122 in
`src/autoskillit/skills_extended/` (Tier 2 and 3). See
[skills/catalog.md](skills/catalog.md).

### How many bundled recipes ship with the plugin?

5: `implementation`, `implementation-groups`, `merge-prs`, `remediation`,
and `research`. See [recipes/overview.md](recipes/overview.md).

### What does the doctor command actually check?

28+ things: base checks span 28 numbered slots (with sub-checks 2b/2c/2d, 4b, 7b) plus up to 5 additional fleet-specific checks. The full table lives in
[installation.md](installation.md#post-install-verification).

### Why are some MCP tools hidden by default?

To keep normal Claude Code sessions clean. The 38 kitchen-tagged tools only
appear after the orchestrator calls `open_kitchen`. See
[execution/tool-access.md](execution/tool-access.md).

### What is the difference between Tier 1, 2, and 3 skills?

Tier 1 lives under `src/autoskillit/skills/` and is plugin-scanned (visible
in plain `claude` sessions). Tier 2 and 3 live under
`src/autoskillit/skills_extended/` and are only revealed inside `cook` or
headless sessions. See [skills/visibility.md](skills/visibility.md).

### Can I override a bundled skill in my project?

Yes. Drop a directory under `.claude/skills/<skill-name>/` or
`.autoskillit/skills/<skill-name>/` containing your `SKILL.md`. The bundled
skill of the same name is shadowed for that project. See
[skills/overrides.md](skills/overrides.md).

### How does AutoSkillit avoid mutating my source tree?

Every recipe run starts by cloning the source repository into
`autoskillit-runs/<run>-<timestamp>/`. The clone's `origin` is rewritten so
the orchestrator can never accidentally push back to the user's working
tree. See [safety/workspace.md](safety/workspace.md).

### What are the 11 `retry_reason` values?

`resume`, `stale`, `none`, `budget_exhausted`, `early_stop`, `zero_writes`,
`empty_output`, `drain_race`, `path_contamination`, `contract_recovery`,
`clone_contamination`. See
[execution/orchestration.md](execution/orchestration.md).

### How do I tune the API quota guard?

Set `quota_guard.short_window_threshold` (default 85.0) for short windows
(e.g. `five_hour`), `quota_guard.long_window_threshold` (default 95.0) for
long windows (weekly, sonnet, opus), and `quota_guard.buffer_seconds`
(default 60) in `.autoskillit/config.yaml`.

### Where do session diagnostics go?

`~/.local/share/autoskillit/logs/` on Linux,
`~/Library/Application Support/autoskillit/logs/` on macOS. The 500 most
recent session directories are kept; older ones are pruned. See
[developer/diagnostics.md](developer/diagnostics.md).

### What should I do when an `implement-worktree-no-merge` session runs out
of context?

The skill returns `needs_retry=true` with the worktree path in the response.
Route to `/autoskillit:retry-worktree` against the same path. Never re-run
`implement-worktree-no-merge` — that creates a new worktree and discards the
partial progress. See [safety/workspace.md](safety/workspace.md).

### How are recipe contracts kept fresh?

Each contract card has a hash recorded in `recipe/staleness_cache.py`. On
recipe load, drifted hashes are enqueued for an LLM-assisted re-check
(`_llm_triage.py`) that compares the deployed `SKILL.md` against the
contract card. See [recipes/authoring.md](recipes/authoring.md).

### Where do I report a bug?

Open an issue in the GitHub repository. AutoSkillit also has a built-in
`report_bug` MCP tool that the `pipeline-summary` skill calls automatically
when an overnight pipeline surfaces a bug. The tool deduplicates against
existing open issues by fingerprint.

### Does AutoSkillit force Claude agent teams off?

No, not by default. The `agent_backend.force_inactive_agent_teams`
configuration option defaults to **false**, scoped per-repository (project
configuration overrides user-level `.claude/settings.json`). When the
operator sets it to **true** on a target repo, AutoSkillit strips
`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` from the launch environment before
every Claude launch (interactive, resume, skill-session, and food-truck
builders) and rewrites any conflicting `env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`
entry in the target repo's `.claude/settings.json` or `.claude/settings.local.json`.
The pre-spawn checkpoint fail-closes: when the effective environment still
carries a truthy team value, the launch refuses with an explicit reason.

### When do teams help, and when is the join contract required?

The force-inactive policy is **not** a global rule — it exists because
join-bearing skills need an unguarded, declared-batch surface that
team-mode routing defeats. Legitimate team workflows (named teammate
dispatch, team-name routing, background tasks) continue to work in any
session that has not loaded a join-bearing skill. A session that loads a
join-bearing skill is permanently bound for the rest of its lifetime: a
later load of a non-join skill cannot downgrade the binding. To re-enable
team workflows in a session, the operator must start a fresh session after
the join-bearing load.

### Can Codex run join-bearing skills?

Not today. Codex's static capability attestation reports
`fixed_set_join_capable=False`. When a skill declares
`semantic_requirements.join.required: true`, the `declare_join_batch` MCP
tool refuses with `unsupported_operation(REQUIRED_JOIN)` and the skill
cannot be admitted. Codex support requires the harness to expose a
fixed-set fan-in primitive; until then, the backend gate is the single
honest source.

### What happens when I try a named Claude dispatch in a join-bound session?

It is denied before child creation. The `background_exec_guard`
PreToolUse hook reads the session binding and rejects any Agent call with
`name`, `team_name`, or `run_in_background` selectors while
`join_required=true`. The denial names the rejected selectors and points
the operator at the `declare_join_batch` gateway — declare a wave with
resolved assignment labels and re-dispatch as ordinary unnamed foreground
Agent calls. The `ScheduleWakeup` deferral hook is denied for the same
reason: deferral cannot produce the declared-batch evidence the join
contract requires.

### What's the difference between `join_required` and `team_name`?

`join_required` is the semantic authority over the **parent's** dispatch
boundary: a parent that loads a join-bearing skill must use the
declared-batch fan-in path; the join contract gates its child routing.
`team_name` is a Claude-only runtime selector that names a teammate under
agent teams. They are not interchangeable: a join-bearing parent cannot
dispatch via `team_name` (the dispatch guard denies it), and a non-join
parent that names a teammate under agent teams is not bound by the join
contract at all — it is the legitimate team workflow path described
above. The two surfaces never overlap in the same session.

