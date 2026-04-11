# Subset Categories

## What Are Subset Categories?

Subset categories are functional domains that group related MCP tools and skills. They are
orthogonal to skill tiers — disabling a subset hides ALL of its members regardless of which
tier they belong to.

**Use case**: A project not using GitHub can disable the entire `github` subset rather than
manually reclassifying each of its tools and skills one by one. A project without CI integration can disable
`ci` to remove CI-polling tools and skills in one line of config.

## Built-in Categories

| Category | MCP Tools | Skills |
|----------|-----------|--------|
| `github` | `fetch_github_issue`, `get_issue_title`, `prepare_issue`, `enrich_issues`, `claim_issue`, `release_issue`, `report_bug`, `get_pr_reviews`, `bulk_close_issues`, `check_pr_mergeable`, `push_to_remote`, `create_unique_branch`, `set_commit_status` | `open-pr`, `open-integration-pr`, `merge-pr`, `review-pr`, `resolve-review`, `analyze-prs`, `prepare-issue`, `enrich-issues`, `process-issues`, `triage-issues`, `collapse-issues`, `issue-splitter`, `report-bug`, `pipeline-summary` |
| `ci` | `wait_for_ci`, `get_ci_status`, `wait_for_merge_queue`, `toggle_auto_merge` | `diagnose-ci` |
| `clone` | `clone_repo`, `remove_clone`, `register_clone_status`, `batch_cleanup_clones` | — |
| `telemetry` | `get_token_summary`, `get_timing_summary`, `write_telemetry_files`, `get_quota_events` | — |
| `arch-lens` | — | All 13 `arch-lens-*` skills, `make-arch-diag`, `verify-diag` |
| `audit` | — | `audit-arch`, `audit-cohesion`, `audit-tests`, `audit-defense-standards`, `audit-bugs`, `audit-friction`, `audit-impl` |

## Disabling a Subset

Add subset names to `subsets.disabled` in your project config:

```yaml
# .autoskillit/config.yaml
subsets:
  disabled:
    - github    # hides all github-tagged tools and skills
    - ci        # hides CI-polling tools and skills
```

**Effect at server startup:**

- **Tools**: `mcp.disable(tags={subset})` is called for each disabled subset, hiding those
  tools before any session can see them
- **Skills**: excluded from the ephemeral session directory at `init_session()` time, so
  they never appear as slash commands
- **`open_kitchen`**: re-disables subset tools after revealing kitchen tools — because
  FastMCP session rules override server rules (see FastMCP Mechanics below)
- **Recipe validation**: `validate_recipe` warns if a recipe references tools or skills
  that belong to a disabled subset

## Custom Tag Groupings

Users can define their own tag groups and optionally disable them:

```yaml
# .autoskillit/config.yaml
subsets:
  disabled:
    - experimental   # a custom tag can also be disabled

  custom_tags:
    my-team-tools:
      - investigate
      - make-plan
      - rectify
    experimental:
      - write-recipe
      - setup-project
```

Custom tags behave like built-in categories for filtering purposes: disabling
`experimental` hides `write-recipe` and `setup-project` from all sessions.

## Configuration Rules

- **Invalid category names**: logged as a warning, not a crash — unrecognized names are
  silently ignored so config stays forward-compatible
- **Absent config**: no behavioral change; nothing is disabled by default
- **Composition with tiers**: disabling a subset removes its members from ALL tiers — a
  Tier 1 skill in a disabled subset is hidden even from plain `$ claude` sessions
- **Re-enabling**: remove the entry from `subsets.disabled` and restart the server to
  restore the original state

## FastMCP Mechanics: Why `open_kitchen` Re-Disables Subsets

FastMCP session rules override server rules. When `open_kitchen` calls
`ctx.enable_components(tags={"kitchen"})` to reveal the 40 kitchen-tagged tools, this
operation overwrites the server-level `mcp.disable(tags={"github"})` mark applied at
startup. As a result, `open_kitchen` must immediately re-call
`ctx.disable_components(tags={subset})` for each configured disabled subset to restore
the correct visibility state.

See `server/tools_kitchen.py` for the implementation.
