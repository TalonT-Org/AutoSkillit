# Getting Started

This tutorial walks through running the `implementation` recipe — AutoSkillit's
flagship pipeline that takes a task description and produces a tested, reviewed PR.

## Before You Start

1. AutoSkillit is installed (`autoskillit doctor` passes)
2. You're in a git repository with a test command configured
3. Your project has `.autoskillit/config.yaml` (from `autoskillit init`)

## Starting the Pipeline

    autoskillit cook implementation

This launches an interactive Claude Code session with the recipe loaded. The
orchestrator will ask you for the required inputs.

## Ingredients (Inputs)

The orchestrator prompts you for these before starting:

| Ingredient | Required | Default | What it does |
|------------|----------|---------|-------------|
| `task` | Yes | — | What to implement. Can be a description or a GitHub issue URL |
| `source_dir` | No | (auto-detected) | Path to your repo. Usually auto-detected |
| `base_branch` | No | `integration` | Branch to merge into (change to `main` if needed) |
| `review_approach` | No | `false` | Research modern solutions before implementing |
| `audit` | No | `true` | Run a quality audit before merging |
| `open_pr` | No | `true` | Open a GitHub PR (vs. direct merge) |
| `issue_url` | No | — | GitHub issue URL to close when done |

### Example inputs

**Simple task:**
> task: "Add a --verbose flag to the export command that prints each file as it's processed"

**GitHub issue:**
> task: "https://github.com/myorg/myproject/issues/42"

## What Happens Next

### 1. Clone

AutoSkillit clones your repository into `../autoskillit-runs/impl-{timestamp}/`.
Your working directory is never modified. All pipeline work happens in the clone.

### 2. Plan

The `make-plan` skill analyzes your codebase deeply:
- Launches parallel subagents to study affected systems
- Researches approaches via web search
- Designs tests first (tests that should fail now, pass after implementation)
- Evaluates approaches on technical merit only
- Generates architecture diagrams using the appropriate lens
- Writes a structured plan to `temp/make-plan/`

If the plan is large (>500 lines), it's automatically split into parts that are
implemented sequentially.

### 3. Verify (Dry Walkthrough)

Before any code is written, the `dry-walkthrough` skill validates the plan:
- Checks that referenced files and functions actually exist
- Validates assumptions about current code state
- Catches circular dependencies and hidden dependencies
- Fixes issues directly in the plan file
- Stamps the plan with `Dry-walkthrough verified = TRUE`

Implementation cannot proceed without this stamp.

### 4. Implement

The `implement-worktree` skill creates a git worktree and implements the plan:
- Creates a new branch in an isolated worktree
- Implements changes phase by phase with commits per phase
- Runs `pre-commit` hooks and the full test suite
- If tests fail, automatically routes to a fix skill

### 5. Test & Fix Loop

If tests fail after implementation:
- The `resolve-failures` skill diagnoses the failures
- It applies fixes and re-runs tests
- This loops until tests pass or the retry budget is exhausted
- If path-prefix-sensitive files are changed, the pipeline may restart from investigation

### 6. Merge

Once tests pass, `merge_worktree` rebases the worktree branch onto the base branch
and fast-forward merges. Tests must pass again after the rebase (test gate).

### 7. Push & PR

The merged changes are pushed to the remote, and a PR is opened. The PR body includes:
- A summary of all plan files used
- Token usage statistics from the pipeline

### 8. Automated Code Review

The `review-pr` skill reviews the PR with 7 parallel audit subagents:

| Audit | What it checks |
|-------|---------------|
| Architecture | Import layering and architectural rule violations |
| Tests | Over-mocking, weak assertions, xdist safety |
| Defense | Typed boundaries, error context, late validation |
| Bugs | Off-by-one errors, missing await, unhandled None |
| Cohesion | Naming consistency, feature locality |
| Slop | Dead code, useless comments, AI backward-compat hacks |
| Deletion regression | Code reintroduced after deliberate deletion |

Each finding is posted as an inline comment on the PR. If changes are requested,
the `resolve-review` skill applies fixes and pushes.

### 9. CI Watch

If CI is configured, AutoSkillit monitors the CI run. If it fails:
- `diagnose-ci` analyzes the failure
- `resolve-failures` applies fixes
- Changes are re-pushed and CI is re-watched

## After the Pipeline

When the pipeline completes, you'll be asked to confirm cleanup. The clone directory
is preserved until you approve deletion.

**If something goes wrong:** Clones are always preserved on failure (`keep: "true"`).
You can inspect the clone, the worktree, and all artifacts in `temp/`.

## Configuration Tips

### Changing the base branch

Most projects use `main` instead of `integration`:

```yaml
# .autoskillit/config.yaml
branching:
  default_base_branch: main
```

Or pass it as an ingredient: `base_branch: main`

### Setting up worktree dependencies

If your project needs setup after creating a worktree (e.g., installing dependencies):

```yaml
worktree_setup:
  command: ["npm", "install"]
```

### Choosing the model

By default, headless sessions use Sonnet. To use a different model:

```yaml
model:
  default: "claude-sonnet-4-6"
```

See [Configuration Reference](configuration.md) for all options.

## Alternative: Chefs-Hat Mode

If you want to use individual skills interactively (without a recipe):

    autoskillit chefs-hat

This launches Claude Code with all 36 bundled skills available as slash commands.
Type `/autoskillit:make-plan` to create a plan, `/autoskillit:investigate` to
debug an issue, etc.
