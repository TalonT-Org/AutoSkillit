<!-- autoskillit-recipe-hash: sha256:b3d23967bfc87d8aa0e2b23fb3b39f46ee8cc063f9a14c28b6efffbea6ce229a -->
<!-- autoskillit-diagram-format: v7 -->
## remediation

### Flow

+-- [investigate] (optional)
|
rectify --- bind_plan_set <-> [bounded coverage replan -> make-plan]
|
[review-approach] (optional)
|
+----+ FOR EACH PLAN PART:
|    |
|    dry-walkthrough --- renew_plan_set --- implement --- test <-> [x fail -> fix]
|             |
|             +-- [context limit -> renew_before_retry -> retry_walkthrough]
|    |
|    +-- [audit] (optional)
|    |     x fail [-> make-plan]
|    |
|    merge
|    |
+----+
     |
     +-- [prepare-pr] (optional)
     |     +-- [arch-lens-{slug}] (optional, one per selected lens, parallel)
     |     compose-pr
