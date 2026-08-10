<!-- autoskillit-recipe-hash: sha256:9bb13321c15b735fae06f70c82a77078a3bdbd87f99c804566ca17842d433b32 -->
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
