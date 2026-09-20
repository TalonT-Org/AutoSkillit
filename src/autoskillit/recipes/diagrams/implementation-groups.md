<!-- autoskillit-recipe-hash: sha256:84238bda46856d6fea15e2f88b78a344cc0530fb31ba5813a4fb31b584cb3959 -->
<!-- autoskillit-diagram-format: v7 -->
## implementation-groups

### Flow

group
|
+----+ FOR EACH GROUP:
|    |
|    plan --- bind_group_parts (open)
|    |
+----+
     |
     seal_plan_set <-> [check_replan_iteration -> gap_replan -> bind_group_parts]
     |
     +----+ FOR EACH PLAN PART:
     |    |
     |    [review-approach] (optional) --- verify --- renew_plan_set
     |    |
     |    implement --- test <-> [x fail -> fix]
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
