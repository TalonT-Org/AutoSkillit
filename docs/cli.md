# CLI Reference

## autoskillit serve

Start the MCP server. This is the default command when no subcommand is given.

    autoskillit serve

You rarely need to run this manually — Claude Code starts the server automatically via the plugin registration.

---

## autoskillit install

Register AutoSkillit as a Claude Code plugin.

    autoskillit install [--scope user|project|local]

**Flags:**
- `--scope` (default: `user`) — Where to install: `user` (global), `project` (per-project), `local`

**What it does:**
1. Creates local marketplace at `~/.autoskillit/marketplace/`
2. Registers marketplace with Claude Code
3. Installs the plugin
4. Syncs hooks to `settings.json`

Syncs hooks and plugin cache. Called automatically by `autoskillit update`.

---

## autoskillit init

Set up a project for AutoSkillit.

    autoskillit init [--force] [--test-command CMD] [--scope user|project]

**Flags:**
- `--force` — Overwrite existing config
- `--test-command` — Set test command non-interactively (e.g., `--test-command "pytest -v"`)
- `--scope` (default: `user`) — Where to register hooks

**Creates:**
- `.autoskillit/config.yaml` — Project configuration
- `.autoskillit/temp/` — Working directory for pipeline artifacts
- MCP server entry in `~/.claude.json`

---

## autoskillit order

Launch an interactive pipeline session.

    autoskillit order [recipe]

**Arguments:**
- `recipe` — Recipe name to run. If omitted, shows an interactive picker.

**Behavior:**
- If no recipe is given, presents a numbered list to choose from (including an "Open kitchen" option)
- Validates the recipe YAML before launching
- Opens a restricted Claude Code session (only `AskUserQuestion` + MCP tools)
- Injects the recipe as the orchestrator's system prompt
- Cannot be run from inside a Claude Code session

**Note:** `order` only accepts recipe names (e.g., `implementation`, `remediation`). Skills like `setup-project` are not recipes — use `autoskillit cook` and then `/autoskillit:setup-project` instead.

**Examples:**

    autoskillit order                    # Interactive picker
    autoskillit order implementation     # Run implementation pipeline
    autoskillit order remediation        # Run remediation pipeline

---

## autoskillit doctor

Run health checks on your setup.

    autoskillit doctor [--output-json]

**Flags:**
- `--output-json` — Output results as JSON

Runs 46 ungated checks (up to 52 with fleet enabled) enumerated by `run_doctor`
in `cli/doctor/__init__.py`: 37 numbered checks (1–23, excluding 5, and 30–44)
and 9 lettered sub-checks (`2b`, `2c`, `2d`, `2e`, `4b`, `7b`, `7c`, `17b`,
`31b`). With fleet enabled the structural total is 43 numbered plus 9 lettered
checks. The checks cover stale MCP
servers, plugin registration, plugin cache existence and integrity, PATH,
project config, secrets placement, shared exact-artifact/install-state consistency,
hook health, hook registration, hook registry drift, recipe version health, gitignore
completeness, secret-scanning hook, editable install source, stale entry
points, source drift, quota cache schema, process state, install classification,
update dismissal state, ambient env leaks, feature gate consistency, codex version,
script binary, claude binary, codex MCP timeouts, codex graduation, CLI conformance,
codex NDJSON drift, codex model-alias staleness, standing backend pin feasibility,
local recipe validity, codex limits pin freshness, bundled skill capability
authenticity, capture-store statistics, project-local skill contracts, and
retained session-index projection consistency, and orphaned codex processes
(including the 6 fleet checks 24–29). See
[installation.md](installation.md#post-install-verification) for the full table.

---

## autoskillit codex-attempts

    autoskillit codex-attempts [--discard-view VIEW_ID --reason TEXT] [--output-json]

**Flags:**
- `--discard-view VIEW_ID` — Explicitly reconcile one eligible retained attempt view
- `--reason TEXT` — Required operator reason when discarding a view
- `--output-json` — Output the listing and reconciliation result as JSON

The default command is read-only: it lists retained Codex attempt views and identifies
which schema-v1 unknown views are eligible for explicit reconciliation. It does not run
automatic recovery. Eligibility requires an identity-consistent manifest in
`running`, `finalizing`, or `failed` state and both staged rollout roots to contain no
descendant entry of any kind.

Discard is fail-closed and affects only the selected view. AutoSkillit records an
immutable audit containing the normalized reason and manifest digest, atomically moves
the view through a deterministic tombstone, and resumes an interrupted tombstone
deletion on retry. Canonical rollout stores and the derived session index are never
modified by this command.

---

## autoskillit codex-orphans

    autoskillit codex-orphans [--reap] [--output-json]

**Flags:**
- `--reap` — Terminate detected orphans (default: report only)
- `--output-json` — Output as JSON

Reports orphaned interactive codex TUI processes — those whose fd 0 resolves to
`/dev/pts/<digits> (deleted)`, the signature of a codex process that survived
destruction of its controlling pty (e.g. VS Code window reload). Linux-only;
same-user scoped (never targets other users' processes). Live-pty codex sessions
and autoskillit-managed headless sessions are excluded by construction (headless
sessions bind stdin to `DEVNULL` or a temp file, never a pty).

With `--reap`, each orphan is re-verified immediately before signaling: starttime
ticks must still match and fd 0 must still be a deleted pty. Termination uses
SIGTERM→SIGKILL process-tree escalation via `kill_process_tree`. Per-target
outcomes:
- **terminated** — bounded observation completed and every verified target is absent
- **skipped** — no longer matches the orphan signature (state-agnostic)
- **incomplete** — escalation left survivors, hit access-denied PIDs, or could
  not complete the bounded observation even when both PID lists are empty

JSON reap entries include `observation_complete` in addition to the action,
survivor PIDs, and access-denied PIDs. Text output renders `observation incomplete`
explicitly when that flag is false.

Reap is signal-only: persisted `~/.codex/sessions` rollouts are never deleted and
remain eligible for `codex resume`. Flushing of an in-flight turn to disk is not
guaranteed by hard termination.

Doctor Check 44 (`orphaned_codex_processes`) surfaces the same detection
read-only and points to this command.

---

## autoskillit cook

Launch Claude Code with all skills as slash commands.

    autoskillit cook

Alias: `autoskillit c`

This gives you an unrestricted Claude session with all bundled skills
available as `/autoskillit:*` slash commands and the kitchen pre-opened.
No recipe — use skills individually as needed.

---

## autoskillit migrate

Check for outdated project recipes and stale project-local skills.

    autoskillit migrate [--check] [--fix]

**Flags:**
- `--check` — Exit with code 1 if any recipes need migration (for CI)
- `--fix` — Apply deterministic project-local skill-contract migrations in
  place (e.g. a pre-contract-era skill copy missing a `uses_capabilities`
  declaration, or missing `semantic_version`)

Recipe migrations are applied automatically when recipes are loaded; this
command just reports what's pending for them. Project-local skills under
`.claude/skills/`, `.autoskillit/skills/`, `.codex/skills/`, and
`.agents/skills/` are different: a stale copy of a bundled skill silently
shadows the bundled version until fixed, so `migrate` also reports which
skills are invalid and why. Without `--fix`, skills are only reported, never
touched. Advisory-only invalidity kinds (frontmatter shape errors, unknown
capabilities, …) are always reported but never auto-fixed — follow the
printed hint instead.

---

## autoskillit quota-status

Check current API quota utilization.

    autoskillit quota-status

Outputs JSON with the current 5-hour rolling utilization percentage.

---

## autoskillit config show

Show the resolved configuration.

    autoskillit config show

Prints the merged result of all config layers as JSON.

---

## autoskillit recipes list

List available recipes.

    autoskillit recipes list

Shows name, source (bundled or project), and description.

---

## autoskillit recipes show

Print a recipe's raw YAML.

    autoskillit recipes show <name>

---

## autoskillit recipes render

Generate flow diagrams for recipes.

    autoskillit recipes render [name]

If no name given, renders all recipes. Diagrams are written to
`recipes/diagrams/{name}.md`.

---

## autoskillit skills list

List all bundled skills.

    autoskillit skills list

Shows name, source, and path for all bundled skills. The complete catalogue is also documented in [skills/catalog.md](skills/catalog.md).

---

## autoskillit workspace init

Create a prep station directory for testing.

    autoskillit workspace init <path>

Creates the directory with a `.autoskillit-workspace` marker that authorizes
`reset_test_dir` and `reset_workspace` to clear it.

---

## autoskillit workspace clean

Prune old run directories.

    autoskillit workspace clean [--dir DIR] [--force]

**Flags:**
- `--dir` — Directory to clean (default: `../autoskillit-runs/`)
- `--force` — Skip confirmation prompt

Removes run directories older than 5 hours.
