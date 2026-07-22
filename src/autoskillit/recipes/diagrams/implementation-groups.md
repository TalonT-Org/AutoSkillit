<!-- autoskillit-recipe-hash: sha256:967971584040943195b7de28f8a41ff8e250ab75bf1994bcabe7896c6b187f37 -->
<!-- autoskillit-diagram-format: v7 -->
## implementation-groups

### Flow

group
|
+----+ FOR EACH GROUP:
|    |
|    plan --- [review-approach] (optional)
|    |
|    +----+ FOR EACH PLAN PART:
|    |    |
|    |    verify --- implement --- test <-> [x fail -> fix]
|    |    |
|    |    merge
|    |    |
|    +----+
|    |
+----+
     |
     +-- [audit] (optional)
     |     x fail [-> plan]
     |
     +-- [prepare-pr] (optional)
     |     +-- [arch-lens-{slug}] (optional, one per selected lens, parallel)
     |     compose-pr
