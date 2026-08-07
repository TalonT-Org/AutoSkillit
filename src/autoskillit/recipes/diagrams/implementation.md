<!-- autoskillit-recipe-hash: sha256:d9a9f35f492a9ffdad8cc638f6d33067ba73c25b8f4c01dc6a709760f97af3d6 -->
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
