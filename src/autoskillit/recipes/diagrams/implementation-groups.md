<!-- autoskillit-recipe-hash: sha256:307f8ff536d30e581b6a419fefb59f2454978e44bfc9513587750ced223a2bba -->
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
