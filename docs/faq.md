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

To keep normal Claude Code sessions clean. The 37 kitchen-tagged tools only
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
