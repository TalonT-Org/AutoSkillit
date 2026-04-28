<!-- autoskillit-recipe-hash: sha256:9628763b1166854e3dc2d955f4f3f71fd9163b3f2e2dd64fd62a0802e1a6702f -->
<!-- autoskillit-diagram-format: v7 -->
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
