## implementation

```
      make-plan
      |
      +-- [review-approach] (optional)
      |
 +----+ FOR EACH PLAN PART:
 |    |
 |    dry-walkthrough --- implement --- test <-> [x fail -> fix]
 |
 +----+
      |
      +-- [audit] (optional)
      |     x fail [-> make-plan]
      |
      +-- [open-pr] (optional)
```
