<!-- autoskillit-recipe-hash: sha256:7b92fe8b75d159ed9f1318163c455912b878c286590ea4182a01bb34e9ebc072 -->
<!-- autoskillit-diagram-format: v7 -->
## implementation
Plan, verify, implement, test, and merge a task end-to-end. Use when user says "run pipeline", "implement task", or "auto implement".

### Graph

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
