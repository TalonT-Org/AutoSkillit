## implementation-groups

```
      make-groups
      |
 +----+ FOR EACH GROUP:
 |    |
 |    make-plan --- [review-approach] (optional) --- dry-walkthrough --- implement --- test <-> [x fail -> fix]
 |
 +----+
      |
      +-- [audit] (optional)
      |     x fail [-> make-plan]
      |
      +-- [open-pr] (optional)
```
