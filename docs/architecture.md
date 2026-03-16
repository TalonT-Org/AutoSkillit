# Architecture

AutoSkillit is a stateless workflow engine built as a Claude Code plugin. It
orchestrates automated pipelines by delegating work to headless Claude Code
sessions, each running a focused skill.

## Core Concepts

### Skills

A skill is a focused task defined in a `SKILL.md` file. Skills run in their own
headless Claude Code session with full tool access. Examples: `make-plan` (create
an implementation plan), `implement-worktree` (implement code in a worktree),
`review-pr` (review a PR with parallel audit subagents).

AutoSkillit bundles 60 skills. Use `autoskillit skills list` to see them all.

### Recipes

A recipe is a YAML workflow that chains skills and MCP tools into an automated
pipeline. Recipes define the step graph (what to run), ingredients (inputs),
routing logic (what to do on success/failure), and kitchen rules (constraints
for the orchestrator).

### The Orchestrator

When you run `autoskillit cook`, an orchestrating Claude Code session reads the
recipe and drives the pipeline. The orchestrator never reads or writes code itself.
It only calls MCP tools: `run_skill` to delegate to headless sessions, `run_cmd`
for shell commands, `test_check` to run tests, etc.

Because the orchestrator only holds the recipe, current step result, and routing
state, its context window stays small. Pipelines have run 48+ hours without context
issues.

## Three-Tier Visibility System

AutoSkillit uses a three-tier visibility model so that its 39 MCP tools and 60
skills never pollute your context window when you don't need them.

### Tier 0: Always Visible (13 tools)

These lightweight tools are visible in every Claude Code session immediately.
They let you inspect recipes, check status, and fetch issues without opening
the kitchen:

`kitchen_status`, `list_recipes`, `load_recipe`, `validate_recipe`,
`get_pipeline_report`, `get_token_summary`, `get_timing_summary`,
`fetch_github_issue`, `get_issue_title`, `get_ci_status`,
`get_quota_events`, `open_kitchen`, `close_kitchen`

### Tier 1: Kitchen-Gated (26 tools)

The pipeline tools — `run_skill`, `run_cmd`, `test_check`, `merge_worktree`,
`clone_repo`, and 21 others — are hidden at startup via FastMCP's tag-based
visibility system. They don't appear in `tools/list`, so Claude never sees
their descriptions and never wastes tokens on them.

To reveal them, call `open_kitchen` (or launch via `autoskillit cook`, which
opens the kitchen automatically). This:

1. Enables the gate (`DefaultGateState.enable()`)
2. Dynamically reveals all 26 kitchen tools to the MCP client
3. Writes a hook config file for the quota guard
4. Injects orchestrator discipline rules into the response

`close_kitchen` reverses this: hides tools, disables the gate, removes config.

### Tier 2: Human-Only Skills

A subset of skills (currently `open-kitchen` and `close-kitchen`) have
`disable-model-invocation: true` injected when running in headless/automated
sessions. This prevents agents from autonomously opening the kitchen or
escalating their own privileges. In `chefs-hat` mode (human-interactive),
this restriction is removed.

### Why This Matters

Most MCP servers dump all their tools into every session, consuming context
window space with tool descriptions the model may never use. AutoSkillit's
gating means you pay zero token cost for pipeline tools during normal coding
sessions. The full pipeline surface only appears when you explicitly ask for it.

## Clone Isolation

Every pipeline that modifies code starts by cloning the source repository into
`../autoskillit-runs/{run_name}-{timestamp}/`. This enforces a strict contract:

- The source directory is never written to during a pipeline
- All implementation happens in the clone
- The clone's `origin` remote is rewritten to point to the real upstream
  (not back to the local source)
- Generated files are decontaminated from the clone's git index

**Safety guards before cloning:**
- Uncommitted changes are detected and the user is asked to choose:
  `proceed` (clone committed state only) or `clone_local` (copy working tree)
- Unpublished branches are detected and warned about

**On failure**, clones are always preserved (`keep: "true"`) for manual inspection.
Cleanup requires explicit user confirmation via a `confirm` action step.

## Headless Session Orchestration

`run_skill` launches a headless Claude Code session for each skill invocation.
The system manages session lifecycle with several reliability mechanisms:

### Completion Marker

Every headless session prompt is appended with a directive to include `%%ORDER_UP%%`
as the last line of output. The orchestrator uses this marker to reliably detect
session completion vs. stale/interrupted sessions.

Recovery paths handle two failure modes:
- **Separate marker**: Model emits the marker as a standalone message (recovered
  by joining all assistant messages)
- **Stale with result**: Session goes stale but stdout contains a valid completed
  result (recovered with `subtype: "recovered_from_stale"`)

### Budget Guard

Consecutive failure counting prevents infinite retry loops. After
`max_consecutive_retries` failures of the same skill, `needs_retry` is forced to
`false` regardless of the session outcome.

### Model Resolution

Three-tier priority: `config.model.override` > per-step `model` field >
`config.model.default`. The override prevents per-step model selection from
escaping a budget constraint.

### Session Diagnostics

On Linux, every headless session captures process-level snapshots (RSS, OOM score,
file descriptors, signals, thread count) at 5-second intervals. Results are written
to `~/.local/share/autoskillit/logs/` with anomaly detection for OOM spikes,
zombies, and resource growth. See [Developer Guide](developer.md) for details.

## Hook System

AutoSkillit registers Claude Code hooks that run on tool calls:

### PreToolUse Hooks (before tool execution)

| Hook | Trigger | What it does |
|------|---------|-------------|
| `skill_cmd_check` | `run_skill` | Validates skill_command argument format |
| `quota_check` | `run_skill` | Blocks when API quota exceeds threshold |
| `skill_command_guard` | `run_skill` | Blocks non-slash skill commands |
| `remove_clone_guard` | `remove_clone` | Blocks deletion unless branch is published |
| `open_kitchen_guard` | `open_kitchen` | Blocks from headless sessions |

### PostToolUse Hook (after tool execution)

| Hook | Trigger | What it does |
|------|---------|-------------|
| `pretty_output` | All AutoSkillit tools | Reformats JSON as Markdown-KV (30-77% token reduction) |

All hooks are stdlib-only (no autoskillit imports) and fail-open — a hook bug never
blocks the user's session.

## Contract Cards

Each recipe has a contract card (generated YAML in `recipes/contracts/`) that
captures the dataflow contract:

- **Skill contracts**: What inputs each skill requires, what outputs it produces
- **Dataflow entries**: Per-step tracking of available/required/produced context variables
- **Staleness detection**: SHA256 hashes of SKILL.md files detect when a skill's
  behavior has drifted from the contract

Two validation rules:
- `contract-unreferenced-required`: A required input is available but not referenced
- `contract-unsatisfied-input`: A required input isn't available at that point

## Safety Features Summary

| Feature | What it prevents |
|---------|-----------------|
| Three-tier visibility | 26 pipeline tools hidden by default — zero token cost in normal sessions |
| Clone isolation | Source repo never modified during pipelines |
| Dry-walkthrough gate | Plans validated before implementation |
| Test gate on merge | Tests must pass before merge is allowed |
| Quota guard | Blocks new sessions when API usage is high |
| Budget guard | Prevents infinite retry loops |
| Remove-clone guard | Clones preserved unless branch is published |
| Open-kitchen guard | Kitchen can't be opened from headless sessions |
| Fail-open hooks | Hook failures never block user sessions |
