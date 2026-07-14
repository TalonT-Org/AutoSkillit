<!-- autoskillit-recipe-hash: sha256:4abd7e21d91878a68cfb9cc0896ff9a84c4a0679e83882ad92094cf8c77c61f2 -->
<!-- autoskillit-diagram-format: v7 -->
## implementation

### Flow

plan --- [review-approach] (optional)
|
+----+ FOR EACH PLAN PART:
|    |
|    verify --- implement --- test <-> [x fail -> fix]
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
