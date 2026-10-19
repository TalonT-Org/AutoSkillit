<!-- autoskillit-recipe-hash: sha256:0512fc264e2a8c29bfd713c6c1631ab38cc4e175f3f2fcbe648ecebc02d28ca8 -->
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
