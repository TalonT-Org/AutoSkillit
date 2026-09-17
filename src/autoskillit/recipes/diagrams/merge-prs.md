<!-- autoskillit-recipe-hash: sha256:92527800f25522e06fdb88704b3a523cdcaccee2f070b685ff175dcc65f9904a -->
<!-- autoskillit-diagram-format: v7 -->
## merge-prs

```text
analyze-prs
    |
    +-- [queue mode]
    |       make-plan -> resolve-merge-conflicts
    |
    +-- [integration mode]
            +----+ FOR EACH PR:
            |    merge-pr
            |        |
            |     make-plan
            |        |
            |     dry-walkthrough
            |        |
            |     implement
            |        |
            |       test <-> [x fail -> fix]
            +----+
                 |
                 +-- [audit] (optional)
                 |      x fail [-> make-plan]
                 |
               open-pr
```
