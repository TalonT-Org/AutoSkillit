<!-- autoskillit-recipe-hash: sha256:a9108b58101949272257c764bb2e0a4830c188cfc950d7c900f0d83347116357 -->
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
