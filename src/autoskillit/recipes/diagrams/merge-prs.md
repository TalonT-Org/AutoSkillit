<!-- autoskillit-recipe-hash: sha256:991256d09d481ccb3c15c33b78c9ac9771d8d8004d6e145114976fe9e598ea9c -->
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
