<!-- autoskillit-recipe-hash: sha256:f4366ef83abf7400ed69fd7d3756a998caa8b1ee7810310d15e04cc5824974b4 -->
<!-- autoskillit-diagram-format: v7 -->
## dev-sprint
Automate a single development sprint: triage open issues, load the implementation-groups recipe for direct orchestration, then create the integration review PR. The orchestrator drives all steps with full visibility.

**Flow:** triage > load-impl-groups (direct orchestration) > open-integration-pr

### Graph
triage  [run_skill] (retry ×3)
│  ↓ success → implement
│  ✗ failure → escalate
│
implement  [load_recipe] (retry ×3)
│  ↓ success → create_pr
│  ✗ failure → escalate
│
create_pr  [run_skill] (retry ×3)
│  ↓ success → done
│  ✗ failure → escalate
│
─────────────────────────────────────
done  "Dev sprint complete. All triaged issue batches have been implemented and the integration review PR has been created. Review the PR before merging. PR URL: ${{ context.pr_url }}
"
escalate  "Dev sprint failed. The pipeline halted at a step that returned failure. Check the logs for the failing step and retry after resolving the issue.
"

### Inputs
| Name | Description | Default |
|------|-------------|---------|
| source_dir | Path to the source repository (auto-detected if empty, uses CWD). | auto-detect |
| base_branch | Integration branch name to merge implementations into. | integration |
| run_name | Pipeline run name prefix for branch and clone naming. | sprint |
### Kitchen Rules
- NEVER use native Claude Code tools (Read, Grep, Glob, Edit, Write, Bash, Agent, WebFetch, WebSearch, NotebookEdit) from the orchestrator. All work is delegated through run_skill.
- Route to on_failure — never investigate or fix failures directly from the orchestrator. The downstream skill handles diagnosis.
- After implement (load_recipe) returns: read the implementation-groups YAML from the tool result and drive each step directly using the orchestrator's kitchen tools. Pass ingredients: source_doc from context.triage_manifest, base_branch from inputs.base_branch, run_name from inputs.run_name. When all implementation-groups steps complete successfully, route to create_pr.
- The create_pr step creates the integration PR combining all individual issue PRs opened during the implementation-groups execution.
