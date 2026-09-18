<!-- autoskillit-recipe-hash: sha256:e036c2e3a456857a476c250e28ce86d60964cf1c55d8cf841e47cd6741c4a478 -->
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
