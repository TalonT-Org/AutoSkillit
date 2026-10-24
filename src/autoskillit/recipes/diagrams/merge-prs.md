<!-- autoskillit-recipe-hash: sha256:47a5c0a0861d2558559decb4f2e5ef85091a853bee4e0719f39282a6435a6809 -->
<!-- autoskillit-diagram-format: v7 -->
## merge-prs

```
          analyze-prs
               |
               +-- queue mode:
               |     +-- ejected conflicts: make-plan -> resolve-merge-conflicts
               |     +-- proactive rebase conflicts: make-plan -> resolve-merge-conflicts
               |     +-- x fail -> diagnose-ci
               |
               +-- classic mode, for each ordered PR:
               |        merge-pr
               |           |
               |           +-- needs plan:
               |              make-plan
               |              +----+ FOR EACH PLAN PART:
               |              |    dry-walkthrough -> implement -> test <-> [x fail -> fix]
               |              +----+
               |
               +-- [audit] (optional)
               |     x fail -> make-plan [-> dry-walkthrough]
               |
            open-pr
               |
               +-- [analyze-pipeline-health] (optional, success or failure)
```
