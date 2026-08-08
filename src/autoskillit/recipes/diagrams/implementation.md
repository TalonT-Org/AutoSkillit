<!-- autoskillit-recipe-hash: sha256:2d819cca3adfd0f4d86f013d6d5f2c17545871f45cf85df21b11eac418c467d1 -->
<!-- autoskillit-diagram-format: v7 -->
## implementation

### Flow

plan --- [review-approach] (optional)
|
+----+ FOR EACH PLAN PART:
|    |
|    verify --- implement --- scope gate --- test <-> [x fail -> fix]
|                       x split -> remove worktree -> plan
|    |
|    merge
|    |
+----+
     |
     +-- [audit] (optional)
     |     x fail [-> plan]
     |
     +-- [prepare-pr] (optional)
     |     +-- [arch-lens-{slug}] (optional, one per selected lens, parallel)
     |     compose-pr
