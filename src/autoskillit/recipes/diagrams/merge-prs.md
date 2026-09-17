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
