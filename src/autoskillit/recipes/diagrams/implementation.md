<!-- autoskillit-recipe-hash: sha256:7d00c7854354f57761c54692254ffe9679ea0290af3bb1f1481b0bf40506b73e -->
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
