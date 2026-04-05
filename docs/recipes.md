# Recipes

Recipes are YAML pipeline definitions that automate multi-step workflows. Each recipe defines a sequence of steps, where each step invokes an MCP tool or a skill.

## Bundled Recipes

AutoSkillit ships with 5 recipes:

### implementation

Plan, implement, test, and open a PR for a task or GitHub issue.

**Flow:** clone → plan → dry-walkthrough → implement → test → merge → audit → push → PR → review → CI → merge queue

**Key ingredients:**

| Ingredient | Default | Description |
|-----------|---------|-------------|
| `task` | *(required)* | What to implement — text description or GitHub issue URL |
| `issue_url` | *(optional)* | GitHub issue URL for branch naming and `Closes #N` |
| `open_pr` | `true` | Create a PR, or merge directly to base branch |
| `audit` | `true` | Run quality audit before merging |
| `auto_merge` | `true` | Enroll PR in merge queue after CI passes |
| `base_branch` | *(auto-detect)* | Branch to merge into |

See **[Getting Started](getting-started.md)** for a complete walkthrough.

### remediation

Investigate a bug deeply, then plan and implement an architectural fix.

**Flow:** clone → investigate → rectify → dry-walkthrough → implement → test → audit → push → PR → review → CI

**When to use:** When you have a bug, regression, or error to fix. Starts with deep investigation and root-cause analysis before planning, unlike `implementation` which plans directly.

**Key ingredients:**

| Ingredient | Default | Description |
|-----------|---------|-------------|
| `task` | *(required)* | Bug description, error message, or traceback |
| `issue_url` | *(optional)* | GitHub issue URL |
| `open_pr` | `true` | Create a PR |
| `audit` | `true` | Run quality audit |

### implementation-groups

Decompose a large document into sequenced groups, then plan and implement each group.

**Flow:** clone → decompose → (per group: plan → dry-walkthrough → implement → test → merge) → audit → push → PR → review → CI

**When to use:** When you have a large architecture proposal, feature spec, or migration plan that's too big to implement in one pass. The `make-groups` skill breaks it into ordered, independently-plannable groups.

**Key ingredients:**

| Ingredient | Default | Description |
|-----------|---------|-------------|
| `source_doc` | *(required)* | Path to the document to decompose |
| `issue_url` | *(optional)* | GitHub issue URL |
| `open_pr` | `true` | Create a PR |
| `audit` | `true` | Run quality audit |

### merge-prs

Consolidate multiple open PRs into a single integration branch and PR.

**Flow:** clone → analyze PRs → (per PR: merge or plan+implement conflicts) → audit → integration PR → review → CI

**When to use:** When you have several open PRs targeting the same branch and want to merge them as a coordinated batch. Handles conflict resolution automatically.

**Two modes:**
- **Classic batch** (default): Creates a per-run integration branch, merges PRs sequentially, opens a single integration PR
- **Queue mode** (auto-detected): When the target branch has a GitHub merge queue, PRs are enqueued directly

**Key ingredients:**

| Ingredient | Default | Description |
|-----------|---------|-------------|
| `base_branch` | *(auto-detect)* | Branch that all PRs target |
| `audit` | `true` | Run quality audit on conflict resolutions |

### research

Two-phase technical research recipe. Phase 1 scopes a research question and opens an experiment design issue for human review. Phase 2 implements the experiment, runs it, writes a report to `research/`, and opens a PR.

**Flow:** scope → plan → issue → (resume with issue_number) → [setup?] → implement → run → report → open-pr

**When to use:** When you want to investigate a technical question, benchmark an approach, or run a reproducible experiment. Produces a structured research report in `research/`.

**Key ingredients:**

| Ingredient | Default | Description |
|-----------|---------|-------------|
| `task` | *(required)* | Research question or topic to investigate |
| `issue_number` | *(optional)* | Approved experiment design issue — skips phase 1 when provided |
| `source_dir` | *(required)* | Path to the project root |
| `base_branch` | `main` | Branch to target for the research PR |
| `setup_phases` | `false` | When `true`, decompose experiment into sequenced setup phases |

**Requires pack:** `research` (pack members: `scope`, `plan-experiment`, `implement-experiment`, `run-experiment`, `write-report`)

## Project Recipes

You can create custom recipes in `.autoskillit/recipes/`:

```yaml
# .autoskillit/recipes/my-workflow.yaml
autoskillit_version: "0.4.0"
name: my-workflow
description: My custom workflow

ingredients:
  task:
    description: What to do
    required: true

steps:
  step_one:
    tool: run_skill
    with:
      skill_command: "/autoskillit:investigate ${{ inputs.task }}"
    capture:
      investigation_path: "${{ result.investigation_path }}"
    on_success: done
    on_failure: escalate

  done:
    action: stop
    message: "Complete."

  escalate:
    action: stop
    message: "Failed."
```

See **[Sub-Recipe Composition](sub-recipe-composition.md)** for advanced patterns like sprint mode.

## Recipe Management

```bash
autoskillit recipes list          # Show all recipes
autoskillit recipes show <name>   # Print raw YAML
autoskillit recipes render <name> # Show flow diagram
autoskillit migrate               # Check for pending recipe migrations
autoskillit migrate --check       # CI-safe: exit 1 if migrations pending
```
