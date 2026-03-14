## merge-prs

```
      analyze-prs
      |
      +--- queue mode:
      |
      |  +----+ FOR EACH PR:
      |  |    |
      |  |    x ejected -> resolve-merge-conflicts
      |  |
      |  +----+
      |
      +--- classic mode:
      |
      |  +----+ FOR EACH PR:
      |  |    |
      |  |    merge-pr
      |  |      x needs_plan -> make-plan --- dry-walkthrough --- implement --- test <-> [x fail -> fix]
      |  |
      |  +----+
      |
      +-- [audit] (optional)
      |     x NO GO [-> make-plan]
      |
      open-integration-pr
      |
      +-- [resolve-merge-conflicts] (on conflict)
```

### Inputs

| Name | Description | Default |
|------|-------------|---------|
| source_dir | Repository path to clone | -- |
| run_name | Run name prefix | pr-merge |
| keep_clone_on_failure | Keep clone on failure | off |
| base_branch | Branch all PRs target | -- |
| upstream_branch | Branch to create base_branch from | main |
| audit | Gate merge on audit-impl check | on |
| plans_dir | Plan files directory for audit-impl | temp/merge-prs |
