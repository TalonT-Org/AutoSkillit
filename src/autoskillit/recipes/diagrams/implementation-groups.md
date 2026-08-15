<!-- autoskillit-recipe-hash: sha256:e0d77ba5723b9dab125ecb27e5a9842a40e761cee039e49c8a07b0e733a42080 -->
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
