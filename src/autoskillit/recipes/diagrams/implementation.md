<!-- autoskillit-recipe-hash: sha256:edae3f172558ff22e0f4093488ec73e9063ddc49186f284abab9e23c7f11fa7a -->
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
