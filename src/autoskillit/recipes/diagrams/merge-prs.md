## merge-prs

```
      analyze-prs
      |
      +--- queue mode:
      |
      |  +----+ FOR EACH PR:
      |  |    |
      |  |    x ejected -> resolve-merge-conflicts
      |  |
      |  +----+
      |
      +--- classic mode:
      |
      |  +----+ FOR EACH PR:
      |  |    |
      |  |    merge-pr
      |  |      x needs_plan -> make-plan --- dry-walkthrough --- implement --- test <-> [x fail -> fix]
      |  |
      |  +----+
      |
      +-- [audit] (optional)
      |     x fail [-> make-plan]
      |
      open-pr
      |
      +-- [resolve-merge-conflicts] (on conflict)
```
