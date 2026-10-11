<!-- autoskillit-recipe-hash: sha256:ced1c3e916e56b77f95f4594729f0581273986e288101b87bba8400536d4cdeb -->
<!-- autoskillit-diagram-format: v7 -->
## implementation

### Flow

plan --- bind_plan_set <-> [bounded coverage replan -> plan]
|
[review-approach] (optional)
|
+----+ FOR EACH PLAN PART:
|    |
|    verify --- renew_plan_set --- implement --- test <-> [x fail -> fix]
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
