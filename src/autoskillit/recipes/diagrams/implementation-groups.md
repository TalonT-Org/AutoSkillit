<!-- autoskillit-recipe-hash: sha256:0f2169fdafce9c8bcf69bc4a32faec5ec56ce56cd09d4da383037ac04874d411 -->
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
