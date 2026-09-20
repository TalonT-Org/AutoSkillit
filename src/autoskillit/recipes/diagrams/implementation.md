<!-- autoskillit-recipe-hash: sha256:dcc564e9169de4e92e278ce14a89b1f75dbd9146fdfe18b7d224f84e0539096c -->
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
