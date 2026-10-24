<!-- autoskillit-recipe-hash: sha256:b19084efdabb952077490668dce7ce61bb194b0b55972c547db84120edc5e57c -->
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
