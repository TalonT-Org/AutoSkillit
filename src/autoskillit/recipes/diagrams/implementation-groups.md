<!-- autoskillit-recipe-hash: sha256:409b554e8b7a4f3243ff0a49724e0ee1b2b5db82cac214f06b32350cbffc7dd4 -->
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
