<!-- autoskillit-recipe-hash: sha256:17c7563c919f74dd0a4b8e8dec8d6ffd0cb5499238620576e579e616472fa5ca -->
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
