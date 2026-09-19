<!-- autoskillit-recipe-hash: sha256:63be3f5ac33e3d4b9f07666ba55722bb918305429dcd5f58e1a23f91e4d6f1cb -->
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
     seal_plan_set <-> [gap_replan -> bind_group_parts]
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
