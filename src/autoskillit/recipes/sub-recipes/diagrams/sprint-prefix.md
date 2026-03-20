## sprint-prefix

```
  triage
  |
  plan  (issue-splitter / collapse-issues / enrich-issues as needed)
  |
  confirm ←— user approval —→ [abort → done]
  |
  dispatch (process-issues: per-issue implementation / remediation)
  |    |
  |  [failure]
  |    |
  report (sprint summary)
```

### Inputs

| Ingredient | Default | Description |
|---|---|---|
| sprint_size | 4 | Max issues in sprint |
| enrich | on | Enrich issues before planning |
| dry_run | off | Triage/selection only, skip dispatch |
| source_dir | auto-detect | Source repository |
| base_branch | auto-detect | Merge target branch |
| run_name | sprint | Run name prefix |
