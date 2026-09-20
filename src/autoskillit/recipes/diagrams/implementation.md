<!-- autoskillit-recipe-hash: sha256:322d4f73bc285e6322116fb0f638a8f8d579002c67fb14627641ccb432f9ab13 -->
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
