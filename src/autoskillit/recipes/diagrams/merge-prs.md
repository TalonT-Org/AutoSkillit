<!-- autoskillit-recipe-hash: sha256:b80f38196338a1b6a3baec05ba1671201eca91a50f12e4736bda95111d76ae41 -->
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
