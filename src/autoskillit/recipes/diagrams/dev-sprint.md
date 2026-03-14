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
