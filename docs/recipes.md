# Recipes

Recipes are YAML workflow definitions that chain skills into automated pipelines.
Each recipe defines ingredients (inputs), steps (tool calls), and routing logic
(what to do on success, failure, or specific result values).

## Bundled Recipes

AutoSkillit ships with 8 recipes:

### implementation

**Use when:** You have a task to implement from scratch — a feature, enhancement, or
refactoring.

**Flow:**
```
Clone ─── Plan ─── Verify ─── Implement ─── Test ─── Merge ─── Push ─── PR ─── Review ─── CI
                                  │           │                         │
                              Dry-walkthrough  Fix loop              7 audit bots
```

**Ingredients:**
| Name | Required | Default | Description |
|------|----------|---------|-------------|
| task | Yes | — | What to implement (text or GitHub issue URL) |
| source_dir | No | auto | Path to source repository |
| base_branch | No | integration | Target branch for PR |
| review_approach | No | false | Research solutions before implementing |
| audit | No | true | Run audit-impl quality gate |
| open_pr | No | true | Open PR vs. direct merge |
| issue_url | No | — | GitHub issue to close |

**Skills invoked:** make-plan, dry-walkthrough, implement-worktree-no-merge,
resolve-failures, audit-impl, open-pr, review-pr, resolve-review, diagnose-ci

---

### bugfix-loop

**Use when:** You have a failing test suite and want automated investigation
and fixing.

**Flow:**
```
Reset ─── Test ─── Investigate ─── Plan ─── Implement ─── Verify ─── Audit ─── Merge
           │                                                 │
        Pass → Done                                     Fix loop
```

**Ingredients:**
| Name | Required | Default | Description |
|------|----------|---------|-------------|
| test_dir | Yes | — | Directory with the failing tests |
| base_branch | No | integration | Target branch |
| helper_dir | Yes | — | Directory for investigation artifacts |
| audit | No | true | Run audit-impl quality gate |

**Skills invoked:** investigate, rectify, implement-worktree-no-merge,
resolve-failures, audit-impl

---

### remediation

**Use when:** You have a problem that needs deep investigation before any
implementation — e.g., a bug report, architectural weakness, or unclear failure.

**Flow:**
```
Clone ─── Investigate ─── Plan ─── Review? ─── Verify ─── Implement ─── Test ─── Audit ─── Merge ─── PR
                                                  │                       │
                                           Verify fail → re-plan      Fix loop
```

**Ingredients:**
| Name | Required | Default | Description |
|------|----------|---------|-------------|
| topic | Yes | — | What to investigate |
| source_dir | No | auto | Path to source repository |
| base_branch | No | integration | Target branch |
| audit | No | true | Run audit-impl quality gate |
| review_approach | No | false | Research solutions |
| open_pr | No | true | Open PR |
| issue_url | No | — | GitHub issue to close |

**Skills invoked:** investigate, rectify, review-approach, dry-walkthrough,
implement-worktree-no-merge, resolve-failures, make-plan, audit-impl, open-pr,
review-pr, resolve-review, diagnose-ci

---

### audit-and-fix

**Use when:** You want to run a code audit and automatically fix the findings.

**Flow:**
```
Clone ─── Audit ─── Investigate ─── Plan ─── Implement ─── Test ─── Merge ─── PR
                                                             │
                                                          Fix loop
```

**Ingredients:**
| Name | Required | Default | Description |
|------|----------|---------|-------------|
| source_dir | No | auto | Path to source repository |
| base_branch | No | integration | Target branch |
| audit_type | No | arch | Type: arch, tests, cohesion, defense-standards |
| open_pr | No | true | Open PR |
| issue_url | No | — | GitHub issue to close |

**Skills invoked:** /audit-{type} (external), investigate, rectify,
implement-worktree-no-merge, resolve-failures, open-pr, review-pr,
resolve-review, diagnose-ci

---

### implementation-groups

**Use when:** You have a large document (roadmap, spec, RFC) that needs to be
broken into sequenced implementation groups.

**Flow:**
```
Clone ─── Decompose ─── [FOR EACH GROUP] ─── Plan ─── Verify ─── Implement ─── Test ─── Merge ─── Audit ─── PR
                              │
                         make-groups
```

**Ingredients:**
| Name | Required | Default | Description |
|------|----------|---------|-------------|
| source_doc | Yes | — | Path to the source document |
| source_dir | No | auto | Path to source repository |
| base_branch | No | integration | Target branch |
| review_approach | No | false | Research solutions |
| audit | No | true | Run audit-impl quality gate |
| open_pr | No | true | Open PR |
| issue_url | No | — | GitHub issue to close |

**Skills invoked:** make-groups, make-plan, review-approach, dry-walkthrough,
implement-worktree-no-merge, resolve-failures, audit-impl, open-pr,
review-pr, resolve-review, diagnose-ci

---

### batch-implementation

**Use when:** You have multiple GitHub issues to implement and want to share
clone setup overhead across all of them instead of cloning once per issue.

**Flow:**
```
Clone ─── [FOR EACH ISSUE] ─── Claim ─── Branch ─── Implement ─── Push ─── PR ─── Release
```

**Ingredients:**
| Name | Required | Default | Description |
|------|----------|---------|-------------|
| issues_file | Yes | — | JSON file with issue objects (`issue_url`, `task`) |
| source_dir | No | auto | Path to source repository |
| run_name | No | batch-impl | Name prefix for this pipeline run |
| base_branch | No | integration | Target branch |

**Skills invoked:** make-plan, dry-walkthrough, implement-worktree-no-merge,
resolve-failures, open-pr

---

### merge-prs

**Use when:** You have multiple open PRs targeting a branch and want to
collapse them into a single integration PR with conflict resolution.

**Flow:**
```
Clone ─── Analyze PRs ─── Create Integration Branch ─── [FOR EACH PR] ─── Merge or Resolve ─── Push ─── Review PR
                                                              │
                                                     Simple → squash merge
                                                     Complex → plan + implement + test
```

**Ingredients:**
| Name | Required | Default | Description |
|------|----------|---------|-------------|
| source_dir | Yes | — | Path to source repository |
| run_name | No | pr-merge | Name prefix for this pipeline run |
| base_branch | No | — | Branch all PRs target |
| upstream_branch | No | main | Branch to create base_branch from if missing |
| audit | No | true | Gate on audit-impl quality check |

**Skills invoked:** analyze-prs, merge-pr, make-plan, dry-walkthrough,
implement-worktree-no-merge, resolve-failures, audit-impl, create-review-pr,
diagnose-ci

---

### smoke-test

**Use when:** You want to verify that the AutoSkillit orchestration engine itself
is working correctly. This is a self-test.

**Flow:**
```
Setup ─── Seed ─── Investigate ─── Plan ─── Implement ─── Test ─── Merge ─── Summary?
                                                           │
                                                        Fix loop
```

**Ingredients:**
| Name | Required | Default | Description |
|------|----------|---------|-------------|
| workspace | Yes | — | An empty directory for the test workspace |
| base_branch | No | main | Base branch |
| collect_on_branch | No | true | Push to feature branch |

---

## Recipe YAML Structure

A recipe file has three top-level sections:

```yaml
# Recipe metadata
name: my-recipe
description: What this recipe does
autoskillit_version: "0.3.1"      # AutoSkillit version it was written for

# Inputs
ingredients:
  - name: task
    description: What to implement
    required: true
  - name: base_branch
    description: Target branch
    default: main

# Rules injected into the orchestrator's system prompt
kitchen_rules:
  - "Never use native Read/Grep/Glob/Edit/Write/Bash tools"
  - "All investigation must go through run_skill"

# The step graph
steps:
  step_name:
    tool: tool_name          # MCP tool to call
    with:                    # Arguments to pass
      key: value
      key2: "${{ inputs.task }}"     # Reference an ingredient
      key3: "${{ context.plan_path }}" # Reference a captured value
    capture:                 # Extract values from the result
      plan_path: "result.plan_path"
    on_success: next_step    # Where to go on success
    on_failure: error_step   # Where to go on failure
```

### Context Variables

Steps can capture values from results and pass them to later steps:

- `${{ inputs.name }}` — References an ingredient value
- `${{ context.name }}` — References a previously captured value
- `${{ result.field }}` — References a field in the current step's result
- `${{ result.stdout | trim }}` — Applies a filter to the result

### Routing

Each step declares where to go next:

- `on_success: step_name` — On successful completion
- `on_failure: step_name` — On failure
- `on_context_limit: step_name` — When the headless session hits context limits
- `on_exhausted: step_name` — When retry attempts are exhausted
- `on_result:` — Multi-way routing based on result values:
  ```yaml
  on_result:
    - when: "result.verdict == 'GO'"
      goto: push
    - when: "result.verdict == 'NO GO'"
      goto: remediate
  ```

### Optional Steps

```yaml
step_name:
    tool: run_skill
    skip_when_false: "${{ inputs.review_approach }}"  # Skip if false
    optional: true
```

`skip_when_false` evaluates the expression. If false, the step is skipped and
routing continues to `on_success`. If the step runs and fails, `on_failure`
routing is still followed — `optional` does not mean failures are tolerated.

### Retries

```yaml
step_name:
    tool: run_skill
    retries: 2                    # Retry up to 2 times
    on_exhausted: escalate        # Where to go when retries run out
```

### Confirm Steps

```yaml
confirm_cleanup:
    action: confirm
    note: "Delete the clone directory?"
    on_success: delete_clone      # User said yes
    on_failure: done              # User said no
```

These use `AskUserQuestion` to get user confirmation before proceeding.

## Project Recipes

Place custom recipes in `.autoskillit/recipes/` to override bundled ones or add
new workflows:

    .autoskillit/recipes/my-custom.yaml

Project recipes with the same name as a bundled recipe take priority.

Generate custom recipes with:
- `/autoskillit:write-recipe` — Create a recipe from a description
- `/autoskillit:setup-project` — Guided setup that generates tailored recipes

## Inspecting Recipes

    autoskillit recipes list              # List all available recipes
    autoskillit recipes show <name>       # Print raw YAML
    autoskillit recipes render [name]     # Generate flow diagrams

## Recipe Migrations

When AutoSkillit is upgraded, project recipes may need migration:

    autoskillit migrate           # Show pending migrations
    autoskillit migrate --check   # Exit 1 if any pending (for CI)

Migrations are applied automatically when recipes are loaded via `load_recipe`.
